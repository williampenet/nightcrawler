"""Events read by a model from "Mes salles" pages that publish no structured data (WIP-66).

PRD FR-1: structured data first, else the page text through the `extract_events` task. The
model is never named here: `extract.extract_events` goes through the task router
(`llm.run_task`, model in `config/models.yaml`, chosen in ADR-0004).

- The configured page(s) are read through the fetcher (robots.txt, per-host interval and
  the site's `Crawl-delay`), with optional `paginate: {param, start, max}` as in
  `listing_jsonld`; pagination stops at an error or at a page already seen.
- Page text is untrusted data: it goes in the user message between markers
  (`extract.messages_for`), the answer must fit the schema, and `extract.check_events`
  keeps only events whose title, day and month are written next to each other in the page.
  Only events the model marks `is_concert` are kept; `events.concert_reason` still applies
  its deterministic vetoes (workshops, theatre...) afterwards.
- `attach: venue` (default): events go to the configured venue, like the other readers.
  `attach: by_location` (aggregators, e.g. Petit Bulletin): each event carries the place
  written on the page and goes through normal venue attribution. The extraction schema has
  no location field yet, so such entries are skipped with a status saying so; adding the
  field needs an eval run first (CLAUDE.md, LLM policy), proposed as a follow-up.
- Cost: the model's raw answer is cached on disk, keyed by the SHA-256 of the page text, the
  model id and revision, and the prompt version (a hash of the system prompt and schema):
  an unchanged page costs no tokens. The grounding checks are re-run on every read. At most
  `llm_pages_per_run` pages per run are sent to the model (cache hits are free).
- No key, or a model error: the venue gets a `skipped: no key` / `error: <Type>` status and
  the run goes on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from .. import extract, llm
from ..events import in_window
from ..http import Fetcher, RobotsBlocked
from ..models import RawEvent
from .listing_jsonld import with_param

log = logging.getLogger(__name__)

SOURCE = "page_llm"
DEFAULT_RUN_CAP = 40
CACHE_MAX_AGE_S = 30 * 86400  # a page unchanged for a month is asked again
MIN_LOCATION_KEY = 4
PROMPT_VERSION = hashlib.sha256(
    (extract.SYSTEM + json.dumps(extract.SCHEMA, sort_keys=True)).encode()
).hexdigest()[:12]


def has_location_field(schema: dict | None = None) -> bool:
    """True once the extraction schema asks the model for each event's place."""
    item = (schema or extract.SCHEMA)["properties"]["events"]["items"]
    return "location" in item.get("properties", {})


def max_chars(task: llm.Task | None) -> int:
    """`limits.max_input_chars` of the task when the router knows it (WIP-63), else the
    extract module's default."""
    return int(getattr(task, "max_input_chars", None) or extract.MAX_CHARS)


class PageBudget:
    """Run-wide cap on pages sent to the model, shared by the reader threads."""

    def __init__(self, limit: int = DEFAULT_RUN_CAP) -> None:
        self.left = limit
        self.used = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.left <= 0:
                return False
            self.left -= 1
            self.used += 1
            return True


class ExtractionCache:
    """Raw model answers on disk (`.cache/llm`, kept between CI runs by actions/cache)."""

    def __init__(self, directory: str | Path | None, max_age: float = CACHE_MAX_AGE_S) -> None:
        self.dir = Path(directory) if directory else None
        self.max_age = max_age
        if self.dir and self.dir.exists():
            cutoff = time.time() - max_age
            for path in self.dir.glob("*.json"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)

    @staticmethod
    def key(text: str, task: llm.Task, venue: str) -> str:
        spec = task.primary
        parts = [
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            task.name,
            spec.provider,
            spec.model,
            spec.revision or "",
            PROMPT_VERSION,
            venue,  # written in the prompt
        ]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()

    def get(self, key: str) -> dict | None:
        if not self.dir:
            return None
        path = self.dir / f"{key}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))["data"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def put(self, key: str, data: dict, model: str) -> None:
        if not self.dir:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f"{key}.{threading.get_ident()}.tmp"
        body = {"model": model, "prompt_version": PROMPT_VERSION, "data": data}
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.dir / f"{key}.json")


@dataclass
class Context:
    """What the page_llm reader needs besides the page: the task, the cache, the budget."""

    task: llm.Task | None
    cache: ExtractionCache = field(default_factory=lambda: ExtractionCache(None))
    budget: PageBudget = field(default_factory=PageBudget)
    client: httpx.Client | None = None

    @classmethod
    def from_config(cls, models_path: str | Path, cache_dir: str | Path | None, cap: int):
        try:
            task = llm.load_tasks(models_path).get(extract.TASK)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("page_llm: no usable model config (%s)", type(exc).__name__)
            task = None
        return cls(task, ExtractionCache(cache_dir), PageBudget(cap))


def fetch_pages(reader: dict, fetcher: Fetcher) -> tuple[list[tuple[str, str]], list[str]]:
    """([(url, html)] of the configured pages and their next pages, notes). A next page
    that fails or repeats one already read ends that URL's pagination."""
    pages: list[tuple[str, str]] = []
    notes: list[str] = []
    seen: set[str] = set()
    errors, first_error = 0, ""
    pager = reader.get("paginate")
    for url in reader["urls"]:
        todo = [url]
        if pager:
            numbers = range(pager["start"], pager["start"] + pager["max"])
            todo += [with_param(url, pager["param"], n) for n in numbers]
        for n, page_url in enumerate(todo):
            try:
                resp = fetcher.get(page_url)
                error = "" if resp.status == 200 else f"HTTP {resp.status}"
            except RobotsBlocked:
                if n == 0:
                    raise  # the configured page itself: the venue is robots_blocked
                error = "robots_blocked"
            except httpx.HTTPError as exc:
                error = type(exc).__name__
            if error:
                if n == 0 or error != "HTTP 404":  # 404 on a next page: past the end
                    errors += 1
                    first_error = first_error or error
                break
            digest = hashlib.sha256(resp.text.encode("utf-8")).hexdigest()
            if digest in seen:  # page 1 served again: past the end
                break
            seen.add(digest)
            pages.append((page_url, resp.text))
        else:
            if pager:
                notes.append(f"page_cap: {url}")
    if errors:
        notes.append(f"page errors: {errors} ({first_error})")
    if not pages:
        raise ValueError(f"no page read ({first_error})")
    return pages, notes


def _start(ev: dict, tz: ZoneInfo) -> datetime:
    hh, mm = (int(x) for x in (ev["time"] or "00:00").split(":"))
    return datetime.combine(date.fromisoformat(ev["date"]), dtime(hh, mm), tzinfo=tz)


def _locations(data: dict, text: str) -> dict[tuple[str, str], str]:
    """{(date, normalised title): place} for answers that carry a place written in the page."""
    flat = extract.norm(text)
    out: dict[tuple[str, str], str] = {}
    for ev in data.get("events") or []:
        place = ev.get("location") if isinstance(ev, dict) else None
        key = extract.norm(place) if isinstance(place, str) else ""
        if len(key) >= MIN_LOCATION_KEY and key in flat:  # grounded in the page
            out[(str(ev.get("date")), extract.norm(str(ev.get("title"))))] = place.strip()
    return out


def read(
    reader: dict,
    venue: str,
    fetcher: Fetcher,
    now: datetime,
    tz: ZoneInfo,
    window_days: int,
    ctx: Context | None = None,
) -> tuple[list[RawEvent], int, str]:
    """(events in the window, pages read, status)."""
    by_location = reader.get("attach") == "by_location"
    if ctx is None or ctx.task is None:
        return [], 0, "skipped: no model config"
    if by_location and not has_location_field():
        return [], 0, "skipped: extraction schema has no location field"
    ok, why = ctx.task.primary.available()
    if not ok:
        return [], 0, "skipped: no key" if why.startswith("no key") else f"skipped: {why}"
    pages, notes = fetch_pages(reader, fetcher)
    host = urlsplit(reader["urls"][0]).netloc.lower()
    source = f"{SOURCE}:{host}"
    today = now.astimezone(tz).date()
    limit = max_chars(ctx.task)
    events: list[RawEvent] = []
    asked = cached = ungrounded = not_concert = no_location = 0
    error = ""
    for url, html in pages:
        text = extract.page_text(html, limit)
        if not text:
            continue
        key = ctx.cache.key(text, ctx.task, venue)
        data = ctx.cache.get(key)
        if data is not None:
            cached += 1
        elif not ctx.budget.take():
            notes.append("llm_cap")  # run-wide cap reached: the next pages wait for a later run
            break
        else:
            asked += 1
            try:
                result = extract.extract_events(text, today, venue, ctx.task, ctx.client)
            except llm.ModelError as exc:
                error = f"error: {type(exc).__name__}"
                log.warning("page_llm %s: %s", host, type(exc).__name__)  # never the message body
                break
            answer = result["answer"]
            data = answer.data
            if data is None:
                notes.append("invalid answer")
                continue
            ctx.cache.put(key, data, answer.model)
        kept, rejected = extract.check_events(data, text, today)
        ungrounded += len(rejected)
        places = _locations(data, text) if by_location else {}
        for ev in kept:
            if not ev["is_concert"]:
                not_concert += 1
                continue
            place = venue
            if by_location:
                place = places.get((ev["date"], extract.norm(ev["title"])))
                if place is None:
                    no_location += 1
                    continue
            raw = RawEvent(
                title=ev["title"],
                start=_start(ev, tz),
                source=source,
                venue_id=source,
                url=url,
                performers=ev["performers"],
                location_name=place,
            )
            if in_window(raw, now, window_days):
                events.append(raw)
    counts = f"model {asked}, cached {cached}, ungrounded {ungrounded}, not concert {not_concert}"
    if by_location:
        counts += f", no location {no_location}"
    status = "; ".join([error or "ok", counts, *notes])
    return events, len(pages), status
