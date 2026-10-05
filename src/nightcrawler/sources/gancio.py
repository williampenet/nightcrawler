"""Events from Gancio community agendas (https://gancio.org), e.g. agenda.villemorte.fr.

Gancio is an open-source federated agenda. `GET <instance>/api/events` lists the upcoming
events with their place (name, address, coordinates) and tags. Descriptions are only on
`/api/event/detail/<slug>`, fetched for a capped number of untagged events whose title
alone cannot decide whether they are concerts.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx

from ..config import Zone
from ..events import concert_reason, in_window
from ..http import Fetcher, RobotsBlocked
from ..models import RawEvent, Venue
from ..structured import _text

log = logging.getLogger(__name__)

MAX_DETAILS = 60  # per instance and per run (responses are cached 20 h by the fetcher)


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _coord(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse(
    items: list, base_url: str, zone: Zone, now: datetime, tz: ZoneInfo
) -> tuple[list[Venue], list[RawEvent]]:
    """Gancio events in the zone and the time window, with their places as venues."""
    base = base_url.rstrip("/")
    host = _host(base)
    venues: dict[str, Venue] = {}
    events: list[RawEvent] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not item.get("slug") or not item.get("title"):
            continue
        try:
            start = datetime.fromtimestamp(int(item["start_datetime"]), UTC).astimezone(tz)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        place = item.get("place")
        if place is None:
            place = {}
        if not isinstance(place, dict):
            continue
        lat, lon = _coord(place.get("latitude")), _coord(place.get("longitude"))
        if lat is not None and lon is not None and not zone.contains(lat, lon):
            continue
        name = _text(place.get("name"))
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        ev = RawEvent(
            title=_text(item["title"]) or "?",
            start=start,
            source=f"gancio:{host}",
            venue_id=f"gancio:{host}:{_place_ref(place, name)}",
            url=f"{base}/event/{quote(str(item['slug']), safe='')}",
            location_name=name,
            tags=[t.strip().lower() for t in tags if isinstance(t, str)],
        )
        if not in_window(ev, now, zone.window_days):
            continue
        if name and lat is not None and lon is not None and ev.venue_id not in venues:
            venues[ev.venue_id] = Venue(
                id=ev.venue_id,
                name=name,
                latitude=lat,
                longitude=lon,
                category="events_venue",
                address=_text(place.get("address")),
                sources=[f"gancio:{host}"],
            )
        events.append(ev)
    return list(venues.values()), events


def _place_ref(place: dict, name: str | None) -> str:
    """The place's Gancio id, else a slug of its name."""
    pid = place.get("id")
    if (isinstance(pid, int) and not isinstance(pid, bool)) or (
        isinstance(pid, str) and pid.strip()
    ):
        return str(pid).strip()
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return "name-" + (re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown")


def needs_detail(ev: RawEvent) -> bool:
    """Untagged, and the title alone does not make it a concert (MAX_DETAILS bounds the cost)."""
    return not ev.tags and concert_reason(ev, None) is None


def _add_details(base: str, events: list[RawEvent], fetcher: Fetcher) -> int:
    fetched = 0
    for ev in sorted(filter(needs_detail, events), key=lambda e: e.start)[:MAX_DETAILS]:
        slug = ev.url.rsplit("/event/", 1)[1]
        try:
            resp = fetcher.get(f"{base}/api/event/detail/{slug}")
            detail = json.loads(resp.text) if resp.status == 200 else {}
        except (httpx.HTTPError, RobotsBlocked, ValueError) as exc:
            log.info("Gancio detail %s: %s", slug, type(exc).__name__)
            continue
        fetched += 1
        if isinstance(detail, dict):
            ev.description = _text(detail.get("description"))
    return fetched


def window_params(zone: Zone, now: datetime) -> dict[str, str]:
    """Gancio `start`/`end` filters (unix seconds), day-aligned so the cache key holds all day.

    Instances that ignore them return all upcoming events; parse() filters the window anyway.
    """
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = today + timedelta(days=zone.window_days + 1)
    return {"start": str(int(today.timestamp())), "end": str(int(end.timestamp()))}


def collect(
    zone: Zone, fetcher: Fetcher, now: datetime, tz: ZoneInfo
) -> tuple[list[Venue], list[RawEvent], str]:
    """Returns venues, events and a status: "skipped", "ok", or "error: <reasons>"."""
    if not zone.gancio_instances:
        return [], [], "skipped"
    venues: list[Venue] = []
    events: list[RawEvent] = []
    errors: list[str] = []
    for inst in zone.gancio_instances:
        base = str(inst["url"]).rstrip("/")
        name = inst.get("name") or _host(base)
        try:
            resp = fetcher.get(f"{base}/api/events", params=window_params(zone, now))
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            v, e = parse(json.loads(resp.text), base, zone, now, tz)
        except (httpx.HTTPError, RobotsBlocked, ValueError) as exc:
            # optional source: one broken instance never stops the run
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            log.warning("Gancio %s: %s", name, reason[:200])
            errors.append(f"{name}: {reason[:200]}")
            continue
        details = _add_details(base, e, fetcher)
        log.info("Gancio %s: %d places, %d events, %d details", name, len(v), len(e), details)
        venues.extend(v)
        events.extend(e)
    return venues, events, ("error: " + "; ".join(errors)) if errors else "ok"
