"""Events read by a model from "Mes salles" pages that publish no structured data (WIP-66).

PRD FR-1: structured data first, else the page text through the `extract_events` task. The
model is never named here: `extract.extract_events` goes through the task router
(`llm.run_task`, model in `config/models.yaml`, chosen in ADR-0004).

- The configured page(s) are read through the fetcher (robots.txt, per-host interval and
  the site's `Crawl-delay`), with optional `paginate: {param, start, max}` as in
  `listing_jsonld`; pagination stops at an error or at a page already seen.
- Long pages are not truncated: the page text is split into chunks of at most `chunk_chars`
  characters (config/zone.yaml, default 3,200: the largest page of the eval set,
  eval/cases.jsonl, is 3,234 characters, so every call stays within the input size the model
  was evaluated on); the task's `max_input_chars` (else extract.MAX_CHARS) is only an upper
  bound. Chunks are cut on line boundaries, preferably just before a date-looking line, and
  repeat `extract.BEFORE` lines (the grounding window above a title) so an event split by a
  cut is whole in the next chunk. At most `llm_chunks_per_page` chunks per page (status
  `chunk_cap` when text is left). One extraction per chunk; results are merged by
  (date, normalised title). La Rayonne's agenda is ~10,700 characters (coordinator,
  2026-10-07): 4-5 chunks depending on where the date lines fall (5 for the test page of
  that size in tests/test_page_llm.py).
- Page text is untrusted data: it goes in the user message between markers
  (`extract.messages_for`), the answer must fit the schema, and `extract.check_events`
  keeps only events whose title, day and month are written next to each other in the chunk
  the event came from. Only events the model marks `is_concert` are kept;
  `events.concert_reason` still applies its deterministic rules afterwards (vetoes for
  workshops and theatre; music words required outside music venues).
- Events go to the configured venue, like the other readers' events.
- Cost: the model's raw answer is cached on disk per chunk (`ExtractionCache.key`): an
  unchanged chunk costs no tokens. Grounding is re-run on every read. At most
  `llm_calls_per_run` chunks per run are sent to the model (cache hits are free).
- An invalid answer (WIP-67) is counted by reason in the status, `invalid answer: truncated
  1, schema 2`: `truncated` (finish_reason "length", or output cut off), `json` (not
  parseable), `schema` (fails validation), `unknown` (no code given). Codes only, never
  page text or model output. The
  chunk is then split once in two on a date line near its middle (`split_chunk`, same
  `OVERLAP_LINES` overlap) and each half is asked once, through the same cache and run-wide
  cap; a half that fails again is counted and left out (never a second split). Each half is
  at most 75% of the chunk's characters, else no split. A split marker is cached under the
  full chunk's key, so later runs go straight to the halves (no call for the full chunk)
  until the entry expires. A smaller
  input also means a shorter answer, so a `truncated` chunk is fixed without raising
  `max_output_tokens` (which would need an eval).
- No key, or a model error: the venue gets a `skipped: no key` / `error: <Type> (transport)`
  status and the run goes on (llm.chat_json already retried transient failures).
- Each kept event's own page (WIP-92, sources/event_page.py): its link is found in the agenda
  HTML without a model (unique match on the title, same host), becomes the event's `url`
  (concert ids do not use it: dedup.concert_id), and the page's JSON-LD description, else its
  main text, becomes the event's description. At most `max_details` pages per venue (reader
  key, default 40) and `DEFAULT_DETAIL_RUN_CAP` per run. Status counts: `links` (events with
  their own page), `detail pages` read, `with text` (events described), `detail errors`.
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
from . import event_page
from .listing_jsonld import with_param

log = logging.getLogger(__name__)

SOURCE = "page_llm"
DEFAULT_RUN_CAP = 40
# event pages per run, all page_llm venues together: the 4 page_llm venues of config/zone.yaml
# (2026-10-09) at the default of 40 each
DEFAULT_DETAIL_RUN_CAP = 160
DEFAULT_CHUNK_CHARS = 3200  # largest eval page: 3,234 characters (eval/cases.jsonl)
DEFAULT_CHUNKS_PER_PAGE = 6
OVERLAP_LINES = extract.BEFORE  # check_events looks this many lines above a title
FULL_TEXT_CHARS = 1_000_000  # page_text cap before chunking (the fetcher caps bodies at 3 MB)
_MONTH_WORDS = "|".join(extract.MONTHS.values())
# "jeu. 08 octobre", "Mercredi 07 oct", "Thu. Nov 12, 2026", "12/11", "1er décembre"
DATE_LINE = re.compile(
    rf"\b\d{{1,2}}(?:er)?\.?\s+(?:{_MONTH_WORDS})\b"
    rf"|\b(?:{_MONTH_WORDS})\.?\s+\d{{1,2}}\b"
    r"|(?<![\d/])\d{1,2}/\d{1,2}(?![\d])"
)
CACHE_MAX_AGE_S = 30 * 86400  # a page unchanged for a month is asked again
# Everything fixed in the request but the chunk, the date and the venue: system prompt,
# schema and the user message template (placeholders filled with constants).
PROMPT_VERSION = hashlib.sha256(
    json.dumps(
        [
            extract.SYSTEM,
            extract.SCHEMA,
            extract.messages_for("{text}", date(2000, 1, 1), "{venue}")[1]["content"],
        ],
        sort_keys=True,
    ).encode()
).hexdigest()[:12]


def max_chars(task: llm.Task | None) -> int:
    """`limits.max_input_chars` of the task when the router knows it (WIP-63), else the
    extract module's default: an upper bound on the chunk size."""
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


MAX_HALF = 0.75  # each half of a split chunk is at most this share of its characters
SPLIT_MARKER = {"_split": True}  # cached under a chunk's key: go straight to its halves
MARKER_MODEL = "split"  # `model` field of a split marker's cache entry


def split_chunk(text: str, overlap: int = OVERLAP_LINES) -> tuple[str, str] | None:
    """Two halves of a chunk whose answer was invalid (WIP-67), or None.

    Cut before a date-looking line: the one nearest the middle among those that leave both
    halves at most MAX_HALF of the chunk's characters (the second half starts `overlap` lines
    before the cut, like chunk_text); else at the middle line if that fits; else None (too
    short, or the overlap is most of the chunk): a split that does not shrink the input
    would not shrink the answer."""
    lines = text.split("\n")
    n, mid = len(lines), len(lines) // 2
    cap = MAX_HALF * len(text)

    def halves(cut: int) -> tuple[str, str] | None:
        if not overlap < cut < n:
            return None
        first, second = "\n".join(lines[:cut]), "\n".join(lines[cut - overlap :])
        return (first, second) if max(len(first), len(second)) <= cap else None

    dated = sorted(
        (i for i in range(overlap + 1, n) if is_date_line(lines[i])),
        key=lambda i: (abs(i - mid), i),
    )
    for cut in [*dated, mid]:
        if parts := halves(cut):
            return parts
    return None


class CallBudget:
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
        """Everything that shapes the answer: chunk, venue, prompt version, models and
        sampling settings. `today` (in the user message) is left out on purpose: it only
        sets the year of dates written without one, so a cached answer stays right from day
        to day, and leaving it in would turn every daily run into a cache miss. Grounding,
        which drops past and far-future dates, is re-run with the real date on every read;
        entries expire after CACHE_MAX_AGE_S."""
        models = [
            [s.provider, s.model, s.revision or "", s.extra]
            for s in (task.primary, task.fallback)
            if s is not None
        ]
        parts = [
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            venue,  # written in the prompt
            task.name,
            PROMPT_VERSION,
            models,
            task.temperature,
            task.max_output_tokens,
        ]
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()

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
    """What the page_llm reader needs besides the page: task, cache, budget, chunking."""

    task: llm.Task | None
    cache: ExtractionCache = field(default_factory=lambda: ExtractionCache(None))
    budget: CallBudget = field(default_factory=CallBudget)
    client: httpx.Client | None = None
    chunks_per_page: int = DEFAULT_CHUNKS_PER_PAGE
    chunk_chars: int = DEFAULT_CHUNK_CHARS
    detail_budget: CallBudget = field(default_factory=lambda: CallBudget(DEFAULT_DETAIL_RUN_CAP))

    @property
    def limit(self) -> int:
        return min(self.chunk_chars, max_chars(self.task))

    @classmethod
    def from_config(
        cls,
        models_path: str | Path,
        cache_dir: str | Path | None,
        calls: int,
        chunks_per_page: int = DEFAULT_CHUNKS_PER_PAGE,
        chunk_chars: int = DEFAULT_CHUNK_CHARS,
    ):
        try:
            task = llm.load_tasks(models_path).get(extract.TASK)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("page_llm: no usable model config (%s)", type(exc).__name__)
            task = None
        return cls(
            task, ExtractionCache(cache_dir), CallBudget(calls), None, chunks_per_page, chunk_chars
        )


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
    if ctx is None or ctx.task is None:
        return [], 0, "skipped: no model config"
    ok, why = ctx.task.primary.available()
    if not ok:
        return [], 0, "skipped: no key" if why.startswith("no key") else f"skipped: {why}"
    pages, notes = fetch_pages(reader, fetcher)
    host = urlsplit(reader["urls"][0]).netloc.lower()
    source = f"{SOURCE}:{host}"
    today = now.astimezone(tz).date()
    events: list[RawEvent] = []
    asked = cached = ungrounded = not_concert = chunk_count = splits = 0
    invalid: dict[str, int] = {}  # reason code -> invalid answers (WIP-67)
    seen: set[tuple[str, str]] = set()  # (date, normalised title): chunks overlap
    error = ""
    capped = False
    hosts = {h for u in reader["urls"] if (h := event_page.host(u))}
    agenda = {event_page.page_key(u) for u in [*reader["urls"], *(u for u, _ in pages)]}
    own: list[RawEvent] = []  # events whose own page was found on the agenda
    for url, html in pages:
        if error or capped:
            break
        links = event_page.page_links(html, url, hosts, agenda)
        chunks, left = chunk_text(
            extract.page_text(html, FULL_TEXT_CHARS), ctx.limit, ctx.chunks_per_page
        )
        if left:
            notes.append(f"chunk_cap: {url}")  # the end of the page is not read
        for chunk in chunks:
            todo = [(chunk, False)]  # (text, is a half of a split chunk)
            while todo and not (error or capped):
                text, half = todo.pop(0)
                data, how, reason = _answer(text, today, venue, ctx)
                if how == "llm_cap":
                    notes.append("llm_cap")  # run-wide cap: the rest waits for a later run
                    capped = True
                    break
                if how.startswith("error"):
                    error = how
                    log.warning("page_llm %s: %s", host, how)  # never the message body
                    break
                chunk_count += not half
                asked += how == "asked"
                cached += how == "cached"
                if data is None or data == SPLIT_MARKER:  # marker: split on an earlier run
                    if data is None:
                        invalid[reason] = invalid.get(reason, 0) + 1
                        log.warning("page_llm %s: invalid answer (%s)", host, reason)  # code
                    if not half and (halves := split_chunk(text)):
                        splits += 1
                        if data is None:  # next runs go straight to the halves
                            key = ctx.cache.key(text, ctx.task, venue)
                            ctx.cache.put(key, SPLIT_MARKER, MARKER_MODEL)
                        todo = [(h, True) for h in halves]  # one split level, never more
                    continue
                kept, rejected = extract.check_events(data, text, today)  # against this text
                ungrounded += len(rejected)
                for ev in kept:
                    key = (ev["date"], extract.norm(ev["title"]))
                    if key in seen:
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
                        location_name=venue,
                        trust_model_concert=reader.get("trust_is_concert") is True,
                    )
                    if in_window(raw, now, window_days):
                        events.append(raw)
                        if link := event_page.event_link(raw.title, links):
                            raw.url = link
                            own.append(raw)
            if error or capped:
                break
    cap = int(reader.get("max_details", event_page.DEFAULT_MAX_DETAILS))
    found, detail_notes = event_page.read_details(own, fetcher, tz, cap, ctx.detail_budget.take)
    notes += detail_notes
    counts = (
        f"chunks {chunk_count}, model {asked}, cached {cached}, ungrounded {ungrounded}, "
        f"not concert {not_concert}, links {len(own)}, detail pages {found.pages}, "
        f"with text {found.described}, detail errors {found.errors}"
    )
    if invalid:
        reasons = ", ".join(f"{r} {n}" for r, n in sorted(invalid.items()))
        notes.append(f"invalid answer: {reasons}")
    if splits:
        notes.append(f"split {splits}")
    status = "; ".join([error or "ok", counts, *notes])
    return events, len(pages), status


def _answer(
    text: str, today: date, venue: str, ctx: Context
) -> tuple[dict | None, str, str | None]:
    """(raw model data or None, how, reason). how: "cached", "asked", "llm_cap" or
    "error: <Type> (transport)"; reason: why an asked answer is invalid (llm.Answer.reason).
    Only answers with no error at all are cached: one that failed a check (mostly
    ungrounded events) is asked again next run."""
    key = ctx.cache.key(text, ctx.task, venue)
    if (data := ctx.cache.get(key)) is not None:
        return data, "cached", None
    if not ctx.budget.take():
        return None, "llm_cap", None
    try:
        result = extract.extract_events(text, today, venue, ctx.task, ctx.client)
    except llm.ModelError as exc:
        return None, f"error: {type(exc).__name__} (transport)", "transport"
    answer = result["answer"]
    if answer.data is not None and not answer.errors:
        ctx.cache.put(key, answer.data, answer.model)
    reason = (answer.reason or "unknown") if answer.data is None else None
    return answer.data, "asked", reason
