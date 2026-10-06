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
import logging
from datetime import datetime, time
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from ..events import in_window
from ..http import Fetcher, RobotsBlocked
from ..models import RawEvent
from ..structured import _text, _url

log = logging.getLogger(__name__)

DEFAULT_PER_PAGE = 50  # items carry SEO blocks: 100 per page could pass the fetcher's 3 MB cap
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
        )
        if in_window(ev, now, window_days):
            events.append(ev)
    return events


def fetch_items(reader: dict, fetcher: Fetcher) -> tuple[list, int, bool]:
    """All items of the endpoint: (items, pages read, stopped by the page cap)."""
    per_page = int(reader.get("per_page", DEFAULT_PER_PAGE))
    max_pages = int(reader.get("max_pages", DEFAULT_MAX_PAGES))
    items: list = []
    for page in range(1, max_pages + 1):
        resp = fetcher.get(reader["url"], params={"per_page": per_page, "page": page})
        if resp.status == 400 and page > 1:  # rest_post_invalid_page_number: past the end
            return items, page - 1, False
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status}")
        data = json.loads(resp.text)
        if not isinstance(data, list):
            raise ValueError("not a list")
        items.extend(data)
        if len(data) < per_page:
            return items, page, False
    return items, max_pages, True


def collect(
    entries: tuple[dict, ...],
    fetcher: Fetcher,
    now: datetime,
    tz: ZoneInfo,
    window_days: int,
) -> tuple[list[RawEvent], list[dict]]:
    """Events of every `wp_json` priority venue and one report row per venue:
    {name, venue, reader, status, events, pages}. A broken endpoint never stops the run."""
    events: list[RawEvent] = []
    rows: list[dict] = []
    for entry in entries:
        reader = entry["reader"]
        row = {"name": entry["name"], "venue": entry["venue"], "reader": reader["type"]}
        if reader["type"] != "wp_json":
            rows.append(row | {"status": "unsupported reader", "events": 0, "pages": 0})
            continue
        try:
            items, pages, capped = fetch_items(reader, fetcher)
            found = parse(items, reader, entry["venue"], now, tz, window_days)
            status = "page_cap" if capped else "ok"
        except RobotsBlocked:
            found, pages, status = [], 0, "robots_blocked"
        except (httpx.HTTPError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            found, pages, status = [], 0, f"error: {reason[:200]}"
        log.info("wp_json %s: %s, %d pages, %d events", entry["name"], status, pages, len(found))
        events.extend(found)
        rows.append(row | {"status": status, "events": len(found), "pages": pages})
    return events, rows
