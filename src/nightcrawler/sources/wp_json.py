"""Events from WordPress REST endpoints (`/wp-json/wp/v2/<post type>`), driven by config.

Some venues publish their agenda as a custom post type with ACF fields (e.g. the
Transbordeur's `evenement`, WIP-56 diagnosis). Which endpoint, which field holds the date,
and its format are listed per venue under `priority_venues` in `config/zone.yaml`; nothing
venue-specific lives here. Pages are read with `per_page`/`page` until a short page, the
HTTP 400 WordPress returns past the last page, or the page cap.

Every value read is untrusted data: titles are reduced to plain text, links must be http(s).
"""

from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ..events import in_window
from ..http import MAX_BYTES, Fetcher
from ..models import RawEvent
from ..structured import MAX_TEXT, _text, _url

# Items carry SEO blocks (`yoast_head`), so 100 per page might pass the fetcher's 3 MB cap
# (unverified: item size not measured, WebFetch truncates long bodies; see `truncated` status).
DEFAULT_PER_PAGE = 50
DEFAULT_MAX_PAGES = 5


def get_path(item: Any, path: str | None) -> Any:
    """Value at a dotted path ("acf.bouton_booking.url"), or None when any step is missing."""
    if not path:
        return None
    value = item
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def get_values(item: Any, path: str | None) -> list[Any]:
    """Every value at a dotted path where a "*" step goes through each item of a list
    ("acf.content.*.description": the description of each ACF layout block)."""
    if not path:
        return []
    values = [item]
    for key in path.split("."):
        nxt: list[Any] = []
        for v in values:
            if key == "*" and isinstance(v, list):
                nxt += v
            elif isinstance(v, dict) and key in v:
                nxt.append(v[key])
        values = nxt
    return [v for v in values if v is not None]


def description(item: Any, path: str | None) -> str | None:
    """The concert's presentation as plain text (HTML stripped by structured._text), blocks
    joined, capped like every listing text (MAX_TEXT). Untrusted data, never instructions."""
    parts = [t for v in get_values(item, path) if isinstance(v, str) and (t := _text(v))]
    return " ".join(" ".join(parts).split())[:MAX_TEXT] or None


def _parse_start(item: dict, reader: dict, tz: ZoneInfo) -> datetime | None:
    fields = reader["fields"]
    raw_date = get_path(item, fields.get("date"))
    if not isinstance(raw_date, str | int) or isinstance(raw_date, bool):
        return None
    try:
        day = datetime.strptime(str(raw_date).strip(), reader["date_format"]).date()
    except ValueError:
        return None
    start = time(0, 0)  # unknown time: dedup treats 00:00 as "any time that day"
    raw_time = get_path(item, fields.get("time"))
    if isinstance(raw_time, str) and raw_time.strip() and reader.get("time_format"):
        try:
            start = datetime.strptime(raw_time.strip(), reader["time_format"]).time()
        except ValueError:
            pass  # keep the day: a bad hour is not a reason to lose the event
    return datetime.combine(day, start, tzinfo=tz)


def parse(
    items: Any,
    reader: dict,
    venue_name: str,
    now: datetime,
    tz: ZoneInfo,
    window_days: int,
) -> list[RawEvent]:
    """RawEvents in the window. Items without a title or a readable date are skipped.

    The events carry the configured venue name as their location; the pipeline attaches
    them to the known venue of that name (events.attribute_venue).
    """
    host = urlsplit(reader["url"]).netloc.lower()
    fields = reader["fields"]
    events: list[RawEvent] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = _text(get_path(item, fields.get("title")))
        start = _parse_start(item, reader, tz)
        if not title or start is None:
            continue
        ev = RawEvent(
            title=title,
            start=start,
            source=f"wp_json:{host}",
            venue_id=f"wp_json:{host}",
            url=_url(get_path(item, fields.get("link"))),
            ticket_url=_url(get_path(item, fields.get("ticket"))),
            location_name=venue_name,
            description=description(item, fields.get("description")),
        )
        if in_window(ev, now, window_days):
            events.append(ev)
    return events


def fetch_items(reader: dict, fetcher: Fetcher) -> tuple[list, int, str]:
    """All items of the endpoint: (items, pages read, status).

    Status: "ok", "page_cap" (more pages may exist) or "truncated" (a page reached the
    fetcher's size cap: it is dropped, the items of earlier pages are kept)."""
    per_page = int(reader.get("per_page", DEFAULT_PER_PAGE))
    max_pages = int(reader.get("max_pages", DEFAULT_MAX_PAGES))
    items: list = []
    for page in range(1, max_pages + 1):
        resp = fetcher.get(reader["url"], params={"per_page": per_page, "page": page})
        if resp.status == 400 and page > 1:  # rest_post_invalid_page_number: past the end
            return items, page - 1, "ok"
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status}")
        if len(resp.text.encode("utf-8")) >= MAX_BYTES:  # cut by the fetcher: not valid JSON
            return items, page - 1, "truncated"
        data = json.loads(resp.text)
        if not isinstance(data, list):
            raise ValueError("not a list")
        items.extend(data)
        if len(data) < per_page:
            return items, page, "ok"
    return items, max_pages, "page_cap"


def read(
    reader: dict,
    venue: str,
    fetcher: Fetcher,
    now: datetime,
    tz: ZoneInfo,
    window_days: int,
) -> tuple[list[RawEvent], int, str]:
    """(events in the window, pages read, status); errors are reported by sources.priority."""
    items, pages, status = fetch_items(reader, fetcher)
    return parse(items, reader, venue, now, tz, window_days), pages, status
