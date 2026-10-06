"""Music events from the Ticketmaster Discovery API (optional: needs TICKETMASTER_API_KEY)."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from ..config import Zone
from ..http import Fetcher
from ..models import RawEvent, Venue
from ..structured import parse_start

log = logging.getLogger(__name__)

API_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
PAGE_SIZE = 200
MAX_PAGES = 5  # the API caps deep paging at 1,000 results


def params_for(zone: Zone, api_key: str, now: datetime, page: int) -> dict[str, str]:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return {
        "apikey": api_key,
        "latlong": f"{zone.latitude},{zone.longitude}",
        "radius": str(int(zone.radius_km)),
        "unit": "km",
        "classificationName": "music",
        "startDateTime": now.astimezone(UTC).strftime(fmt),
        "endDateTime": (now + timedelta(days=zone.window_days)).astimezone(UTC).strftime(fmt),
        "size": str(PAGE_SIZE),
        "page": str(page),
        "sort": "date,asc",
        # the API defaults to locale "en", which hides events published only in French
        "locale": "*",
    }


def _address(tv: dict) -> str | None:
    parts = ((tv.get("address") or {}).get("line1"), (tv.get("city") or {}).get("name"))
    return ", ".join(p for p in parts if p) or None


def parse(payload: dict, tz: ZoneInfo) -> tuple[list[Venue], list[RawEvent]]:
    venues: dict[str, Venue] = {}
    events: list[RawEvent] = []
    for item in (payload.get("_embedded") or {}).get("events", []):
        tm_venues = (item.get("_embedded") or {}).get("venues") or []
        if not tm_venues:
            continue
        tv = tm_venues[0]
        loc = tv.get("location") or {}
        if "latitude" not in loc:
            continue
        vid = f"tm:{tv['id']}"
        if vid not in venues:
            venues[vid] = Venue(
                id=vid,
                name=tv.get("name", "?"),
                latitude=float(loc["latitude"]),
                longitude=float(loc["longitude"]),
                category="music_venue",
                website=None,  # tv["url"] is a Ticketmaster page, not the venue site
                address=_address(tv),
                sources=["ticketmaster"],
            )
        dates = (item.get("dates") or {}).get("start") or {}
        start = parse_start(dates.get("dateTime") or dates.get("localDate"), tz)
        if not start:
            continue
        attractions = (item.get("_embedded") or {}).get("attractions") or []
        events.append(
            RawEvent(
                title=item.get("name", "?"),
                start=start,
                source="ticketmaster",
                venue_id=vid,
                url=item.get("url"),
                ticket_url=item.get("url"),
                performers=[a["name"] for a in attractions if a.get("name")],
                types=["MusicEvent"],
            )
        )
    return list(venues.values()), events


def collect(
    zone: Zone, fetcher: Fetcher, now: datetime, tz: ZoneInfo
) -> tuple[list[Venue], list[RawEvent], str]:
    """Returns venues, events and a status: "skipped", "ok", or "error: <reason>"."""
    api_key = os.environ.get("TICKETMASTER_API_KEY")
    if not api_key:
        log.info("Ticketmaster: skipped (TICKETMASTER_API_KEY not set)")
        return [], [], "skipped"
    status = "ok"
    all_venues: dict[str, Venue] = {}
    all_events: list[RawEvent] = []
    for page in range(MAX_PAGES):
        try:
            resp = fetcher.get(
                API_URL,
                check_robots=False,
                use_cache=False,  # the URL carries the API key: never write it to disk
                params=params_for(zone, api_key, now, page),
            )
            if resp.status != 200:
                log.warning("Ticketmaster: HTTP %s, stopping", resp.status)
                status = f"error: HTTP {resp.status}"
                break
            payload = json.loads(resp.text)
        except (httpx.HTTPError, ValueError) as exc:
            # optional source: never stop the run; never log the URL (it holds the key)
            log.warning("Ticketmaster: %s, stopping", type(exc).__name__)
            status = f"error: {type(exc).__name__}"
            break
        venues, events = parse(payload, tz)
        all_venues.update({v.id: v for v in venues})
        all_events.extend(events)
        total_pages = (payload.get("page") or {}).get("totalPages", 0)
        if page + 1 >= total_pages:
            break
    log.info("Ticketmaster: %d venues, %d events", len(all_venues), len(all_events))
    return list(all_venues.values()), all_events, status
