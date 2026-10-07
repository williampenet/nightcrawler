"""The "Mes salles" read through the reader configured under `priority_venues` (PRD FR-1).

Each reader is `read(reader, venue, fetcher, now, tz, window_days) -> (events, pages, status)`.
Venues are read in parallel (the fetcher rate-limits per host); one report row per venue:
{name, venue, reader, status, events, pages}. A broken venue never stops the run.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from ..http import Fetcher, RobotsBlocked
from ..models import RawEvent
from . import listing_jsonld, wp_json

log = logging.getLogger(__name__)

READERS = {"wp_json": wp_json.read, "listing_jsonld": listing_jsonld.read}
WORKERS = 4


def _read_one(
    entry: dict, fetcher: Fetcher, now: datetime, tz: ZoneInfo, window_days: int
) -> tuple[list[RawEvent], dict]:
    reader = entry["reader"]
    row = {"name": entry["name"], "venue": entry["venue"], "reader": reader["type"]}
    read = READERS.get(reader["type"])
    if read is None:
        return [], row | {"status": "unsupported reader", "events": 0, "pages": 0}
    try:
        found, pages, status = read(reader, entry["venue"], fetcher, now, tz, window_days)
    except RobotsBlocked:
        found, pages, status = [], 0, "robots_blocked"
    except (httpx.HTTPError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        found, pages, status = [], 0, f"error: {reason[:200]}"
    except Exception as exc:  # unexpected data shape: this venue only
        found, pages, status = [], 0, f"error: {type(exc).__name__}"
    log.info(
        "%s %s: %s, %d pages, %d events", reader["type"], entry["name"], status, pages, len(found)
    )
    return found, row | {"status": status, "events": len(found), "pages": pages}


def collect(
    entries: tuple[dict, ...],
    fetcher: Fetcher,
    now: datetime,
    tz: ZoneInfo,
    window_days: int,
) -> tuple[list[RawEvent], list[dict]]:
    """Events of every priority venue and one report row per venue, in config order."""
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda e: _read_one(e, fetcher, now, tz, window_days), entries))
    events = [ev for found, _ in results for ev in found]
    return events, [row for _, row in results]
