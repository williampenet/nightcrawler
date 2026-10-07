"""Events read by a model from "Mes salles" pages that publish no structured data (WIP-66).

PRD FR-1: structured data first, else the page text through the `extract_events` task. The
model is never named here: `extract.extract_events` goes through the task router
(`llm.run_task`, model in `config/models.yaml`, chosen in ADR-0004).

- The configured page(s) are read through the fetcher (robots.txt, per-host interval and
  the site's `Crawl-delay`), with optional `paginate: {param, start, max}` as in
  `listing_jsonld`; pagination stops at an error or at a page already seen.
- Long pages are not truncated: the page text is split into chunks of at most
  `max_input_chars` (the evaluated input size, so the eval conditions hold for every call),
  cut on line boundaries, preferably just before a date-looking line, with OVERLAP_LINES
  lines repeated; at most `llm_chunks_per_page` chunks per page (status `chunk_cap` when
  text is left). One extraction per chunk; results are merged by (date, normalised title).
  La Rayonne's agenda is ~10,700 characters (coordinator, 2026-10-07): its Nov-Dec
  concerts sit past the first 7,000.
- Page text is untrusted data: it goes in the user message between markers
  (`extract.messages_for`), the answer must fit the schema, and `extract.check_events`
  keeps only events whose title, day and month are written next to each other in the chunk
  the event came from.
  Only events the model marks `is_concert` are kept; `events.concert_reason` still applies
  its deterministic vetoes (workshops, theatre...) afterwards.
- `attach: venue` (default): events go to the configured venue, like the other readers.
  `attach: by_location` (aggregators, e.g. Petit Bulletin): each event carries the place
  written on the page and goes through normal venue attribution. The extraction schema has
  no location field yet, so such entries are skipped with a status saying so; adding the
  field needs an eval run first (CLAUDE.md, LLM policy), proposed as a follow-up.
- Cost: the model's raw answer is cached on disk per chunk, keyed by the SHA-256 of the
  chunk text, the model id and revision, and the prompt version (a hash of the system prompt
  and schema): an unchanged chunk costs no tokens. The grounding checks are re-run on every
  read. At most `llm_pages_per_run` chunks per run are sent to the model (cache hits free).
- No key, or a model error: the venue gets a `skipped: no key` / `error: <Type>` status and
  the run goes on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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
DEFAULT_CHUNKS_PER_PAGE = 4
OVERLAP_LINES = 3  # repeated at the start of the next chunk: an event cut in two stays whole
FULL_TEXT_CHARS = 1_000_000  # page_text cap before chunking (the fetcher caps bodies at 3 MB)
_MONTH_WORDS = "|".join(extract.MONTHS.values())
# "jeu. 08 octobre", "Mercredi 07 oct", "Thu. Nov 12, 2026", "12/11", "1er décembre"
DATE_LINE = re.compile(
    rf"\b\d{{1,2}}(?:er)?\.?\s+(?:{_MONTH_WORDS})\b"
    rf"|\b(?:{_MONTH_WORDS})\.?\s+\d{{1,2}}\b"
    r"|(?<![\d/])\d{1,2}/\d{1,2}(?![\d])"
)
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


def is_date_line(line: str) -> bool:
    return bool(DATE_LINE.search(extract.plain(line)))


def chunk_text(
    text: str, limit: int, max_chunks: int, overlap: int = OVERLAP_LINES
) -> tuple[list[str], bool]:
    """(chunks of at most `limit` characters, True when text was left after `max_chunks`).

    Cuts on line boundaries, at the last date-looking line of the second half of the chunk
    when there is one (an event block usually starts with its date), else where the limit
    falls. The next chunk starts `overlap` lines before the cut (never at or before the
    previous start, so the loop always moves on). Lines are at most extract.MAX_LINE + 1
    characters (page_text), far below any usable limit.
    """
    lines = text.split("\n") if text else []
    chunks: list[str] = []
    start = 0
    while start < len(lines):
        if len(chunks) == max_chunks:
            return chunks, True
        end, size = start, 0
        while end < len(lines) and size + len(lines[end]) + (end > start) <= limit:
            size += len(lines[end]) + (end > start)
            end += 1
        end = max(end, start + 1)  # a single over-long line still makes progress
        cut = end
        if end < len(lines):
            half = start + (end - start) // 2
            dated = [i for i in range(end, half, -1) if is_date_line(lines[i])]
            if dated and dated[0] > start:
                cut = dated[0]
        chunks.append("\n".join(lines[start:cut])[:limit])
        if cut >= len(lines):
            break
        start = max(cut - overlap, start + 1)
    return chunks, False


class PageBudget:
    """Run-wide cap on chunks sent to the model, shared by the reader threads."""

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
    chunks_per_page: int = DEFAULT_CHUNKS_PER_PAGE

    @classmethod
    def from_config(
        cls,
        models_path: str | Path,
        cache_dir: str | Path | None,
        cap: int,
        chunks_per_page: int = DEFAULT_CHUNKS_PER_PAGE,
    ):
        try:
            task = llm.load_tasks(models_path).get(extract.TASK)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("page_llm: no usable model config (%s)", type(exc).__name__)
            task = None
        return cls(task, ExtractionCache(cache_dir), PageBudget(cap), None, chunks_per_page)


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
    asked = cached = ungrounded = not_concert = no_location = chunk_count = 0
    seen: set[tuple[str, str]] = set()  # (date, normalised title): chunks overlap
    error = ""
    capped = False
    for url, html in pages:
        if error or capped:
            break
        chunks, left = chunk_text(
            extract.page_text(html, FULL_TEXT_CHARS), limit, ctx.chunks_per_page
        )
        if left:
            notes.append(f"chunk_cap: {url}")  # the end of the page is not read
        for text in chunks:
            data, failure = _answer(text, today, venue, ctx)
            if failure == "llm_cap":
                notes.append("llm_cap")  # run-wide cap: the rest waits for a later run
                capped = True
                break
            if failure.startswith("error"):
                error = failure
                log.warning("page_llm %s: %s", host, failure)  # never the message body
                break
            chunk_count += 1
            asked += failure == "asked"
            cached += failure == "cached"
            if data is None:
                notes.append("invalid answer")
                continue
            kept, rejected = extract.check_events(data, text, today)  # against this chunk
            ungrounded += len(rejected)
            places = _locations(data, text) if by_location else {}
            for ev in kept:
                key = (ev["date"], extract.norm(ev["title"]))
                if key in seen:
                    continue
                place = places.get(key) if by_location else venue
                if place is None:  # maybe placed in the overlapping chunk: not marked seen
                    no_location += 1
                    continue
                seen.add(key)
                if not ev["is_concert"]:
                    not_concert += 1
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
    counts = (
        f"chunks {chunk_count}, model {asked}, cached {cached}, ungrounded {ungrounded}, "
        f"not concert {not_concert}"
    )
    if by_location:
        counts += f", no location {no_location}"
    status = "; ".join([error or "ok", counts, *notes])
    return events, len(pages), status


def _answer(text: str, today: date, venue: str, ctx: Context) -> tuple[dict | None, str]:
    """(raw model data or None, how): "cached", "asked", "llm_cap" or "error: <Type>"."""
    key = ctx.cache.key(text, ctx.task, venue)
    if (data := ctx.cache.get(key)) is not None:
        return data, "cached"
    if not ctx.budget.take():
        return None, "llm_cap"
    try:
        result = extract.extract_events(text, today, venue, ctx.task, ctx.client)
    except llm.ModelError as exc:
        return None, f"error: {type(exc).__name__}"
    answer = result["answer"]
    if answer.data is not None:
        ctx.cache.put(key, answer.data, answer.model)
    return answer.data, "asked"


def capture(
    entries: tuple[dict, ...],
    fetcher: Fetcher,
    now: datetime,
    limit: int,
    chunks_per_page: int = DEFAULT_CHUNKS_PER_PAGE,
) -> tuple[list[dict], list[str]]:
    """The chunks of every page_llm page, exactly as the model gets them, for the eval set:
    ([{id, venue, url, captured, text}], [error lines]); id = <name>-p<page>-c<chunk>.
    One broken venue never stops it. No model is called."""
    out: list[dict] = []
    errors: list[str] = []
    for entry in entries:
        if entry["reader"]["type"] != SOURCE:
            continue
        try:
            pages, _ = fetch_pages(entry["reader"], fetcher)
        except (RobotsBlocked, httpx.HTTPError, ValueError) as exc:
            errors.append(f"{entry['name']}: {type(exc).__name__}")
            continue
        for n, (url, html) in enumerate(pages, start=1):
            text = extract.page_text(html, FULL_TEXT_CHARS)
            chunks, _ = chunk_text(text, limit, chunks_per_page)
            for k, chunk in enumerate(chunks, start=1):
                out.append(
                    {
                        "id": f"{extract.norm(entry['name'])}-p{n}-c{k}",
                        "venue": entry["name"],
                        "url": url,
                        "captured": now.isoformat(timespec="seconds"),
                        "text": chunk,
                    }
                )
    return out, errors
