"""Events from Gancio community agendas (https://gancio.org), e.g. agenda.villemorte.fr.

Gancio is an open-source federated agenda. `GET <instance>/api/events` lists the upcoming
events with their place (name, address, coordinates) and tags. Descriptions are only on
`/api/event/detail/<slug>`, fetched for a capped number of events: first the untagged ones
whose title alone cannot decide whether they are concerts, then the others, whose text is
what the taste judge reads (WIP-91: 38 of 110 Gancio concerts had their title only).
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import httpx

from ..config import Zone
from ..events import concert_reason, in_window, tag_reason
from ..http import Fetcher, RobotsBlocked
from ..lineup import description_acts
from ..models import RawEvent, Venue
from ..structured import _text

log = logging.getLogger(__name__)

# per instance and per run. One instance is configured (config/zone.yaml), with 186 events in
# the 60-day window of Pipeline 37918562968 and 110 published concerts: 120 covered the soonest
# ones, not all. With the 400-day horizon (WIP-107) a detail can decide whether an untagged event
# is a concert (needs_detail, read first), so the cap is one per day of the horizon: 400 s at
# most at 1 request / s, read alongside the venue probe (pipeline.run). The fetcher cache (20 h)
# does not span two daily runs, so every run asks again. A hit is `detail_cap` in the status.
MAX_DETAILS = 400
MAX_GEOCODE = 60  # places without coordinates, per run
# French national address API (IGN Géoplateforme, BAN data): free, no key, public service
GEOCODER_URL = "https://data.geopf.fr/geocodage/search"
MIN_GEOCODE_SCORE = 0.6


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _coord(value) -> float | None:
    if isinstance(value, bool):  # float(True) == 1.0: not a coordinate
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _point(place: dict) -> tuple[float | None, float | None]:
    """The place's (lat, lon), or (None, None) when unknown.

    Some instances store unknown places at (0, 0) ("null island", e.g. Grrrnd Zero on
    agenda.villemorte.fr): that is a missing value, not a point outside the zone.
    """
    lat, lon = _coord(place.get("latitude")), _coord(place.get("longitude"))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None, None
    return lat, lon


def parse(
    items: list,
    base_url: str,
    zone: Zone,
    now: datetime,
    tz: ZoneInfo,
    geocode: Callable[[str], tuple[float, float] | None] | None = None,
) -> tuple[list[Venue], list[RawEvent]]:
    """Gancio events in the zone and the time window, with their places as venues.

    Many instances store places without coordinates: `geocode(address)` fills them in.
    """
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
        lat, lon = _point(place)
        address = _text(place.get("address"))
        if lat is not None and lon is not None and not zone.contains(lat, lon):
            continue  # the instance itself places it outside the zone
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
        if (lat is None or lon is None) and name and address and geocode:
            found = geocode(address)
            # a guessed point outside the zone is more likely a wrong match than a far-away
            # event: keep the event, without a venue
            if found and zone.contains(*found):
                lat, lon = found
        if name and lat is not None and lon is not None and ev.venue_id not in venues:
            venues[ev.venue_id] = Venue(
                id=ev.venue_id,
                name=name,
                latitude=lat,
                longitude=lon,
                category="events_venue",
                address=address,
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


def detail_order(events: list[RawEvent], cap: int | None = None) -> list[RawEvent]:
    """Events to read in detail, at most `cap` (MAX_DETAILS): those whose kind needs the
    description first, then every other one without a description (the judge's text), soonest
    first. Events whose tags already drop them (théâtre, atelier…) are never read."""
    linked = [
        e
        for e in events
        if e.url and "/event/" in e.url and not e.description and tag_reason(e.tags) != ""
    ]
    first = sorted(filter(needs_detail, linked), key=lambda e: e.start)
    rest = sorted((e for e in linked if not needs_detail(e)), key=lambda e: e.start)
    return (first + rest)[: MAX_DETAILS if cap is None else cap]


def _add_details(base: str, events: list[RawEvent], fetcher: Fetcher) -> tuple[int, str]:
    """(details fetched, "" or the cap note "detail_cap: <cap> of <wanted>")."""
    fetched = 0
    wanted = len(detail_order(events, cap=len(events)))
    note = f"detail_cap: {MAX_DETAILS} of {wanted}" if wanted > MAX_DETAILS else ""
    for ev in detail_order(events):
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
            # "Name (genre, country)" lines of the first paragraph join the line-up (WIP-72)
            ev.billed = description_acts(detail.get("description"))
    return fetched, note


def fetch_details(zone: Zone, fetcher: Fetcher, events: list[RawEvent]) -> list[str]:
    """Detail texts of the events of each configured instance (collect() lists them; the
    pipeline reads these alongside the venue probe). Returns the cap notes,
    "<instance>: detail_cap: ..." (names from config/zone.yaml)."""
    notes: list[str] = []
    for inst in zone.gancio_instances:
        base = str(inst["url"]).rstrip("/")
        mine = [ev for ev in events if (ev.url or "").startswith(base + "/event/")]
        fetched, note = _add_details(base, mine, fetcher)
        log.info("Gancio %s: %d details", inst.get("name") or _host(base), fetched)
        if note:
            notes.append(f"{inst.get('name') or _host(base)}: {note}")
    return notes


def window_params(zone: Zone, now: datetime) -> dict[str, str]:
    """Gancio `start`/`end` filters (unix seconds), day-aligned so the cache key holds all day.

    Instances that ignore them return all upcoming events; parse() filters the window anyway.
    """
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = today + timedelta(days=zone.window_days + 1)
    return {"start": str(int(today.timestamp())), "end": str(int(end.timestamp()))}


class Geocoder:
    """Address -> (lat, lon) with the national address API; capped, memoised per run."""

    def __init__(
        self, fetcher: Fetcher, limit: int = MAX_GEOCODE, near: tuple[float, float] | None = None
    ):
        self.fetcher, self.limit, self.near = fetcher, limit, near
        self.memo: dict[str, tuple[float, float] | None] = {}
        self.calls = self.found = 0
        self.refused: set[str] = set()  # addresses left without coordinates by the cap

    def __call__(self, address: str) -> tuple[float, float] | None:
        key = re.sub(r"\s+", " ", address).strip()
        if key in self.memo:
            return self.memo[key]
        if self.calls >= self.limit:
            self.refused.add(key)  # a set: an address asked twice is one place
            return None
        self.calls += 1
        result = None
        try:
            params = {"q": key[:200], "limit": "1"}
            if self.near:  # ranks results near the zone first
                params.update(lat=str(self.near[0]), lon=str(self.near[1]))
            resp = self.fetcher.get(GEOCODER_URL, params=params)
            feats = json.loads(resp.text).get("features") if resp.status == 200 else None
            best = feats[0] if feats else None
            if best and float(best["properties"].get("score", 0)) >= MIN_GEOCODE_SCORE:
                lon, lat = (float(x) for x in best["geometry"]["coordinates"][:2])
                if math.isfinite(lat) and math.isfinite(lon):
                    result = (lat, lon)
        except (httpx.HTTPError, RobotsBlocked, ValueError, KeyError, TypeError, AttributeError):
            log.info("geocoding failed for a Gancio place")
        self.found += result is not None
        self.memo[key] = result
        return result


def collect(
    zone: Zone, fetcher: Fetcher, now: datetime, tz: ZoneInfo
) -> tuple[list[Venue], list[RawEvent], str]:
    """Returns venues, events and a status: "skipped", "ok", or "error: <reasons>"; a geocoder
    cap hit is appended ("ok; geocode_cap: 60 of 64"). Detail texts are read by fetch_details."""
    if not zone.gancio_instances:
        return [], [], "skipped"
    venues: list[Venue] = []
    events: list[RawEvent] = []
    errors: list[str] = []
    notes: list[str] = []
    geocode = Geocoder(fetcher, near=(zone.latitude, zone.longitude))
    for inst in zone.gancio_instances:
        base = str(inst["url"]).rstrip("/")
        name = inst.get("name") or _host(base)
        try:
            resp = fetcher.get(f"{base}/api/events", params=window_params(zone, now))
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            v, e = parse(json.loads(resp.text), base, zone, now, tz, geocode)
        except (httpx.HTTPError, RobotsBlocked, ValueError) as exc:
            # optional source: one broken instance never stops the run
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            log.warning("Gancio %s: %s", name, reason[:200])
            errors.append(f"{name}: {reason[:200]}")
            continue
        log.info("Gancio %s: %d places, %d events", name, len(v), len(e))
        venues.extend(v)
        events.extend(e)
    log.info("Gancio geocoding: %d/%d addresses found", geocode.found, geocode.calls)
    if geocode.refused:  # addresses left without coordinates by the cap (WIP-107)
        notes.append(f"geocode_cap: {geocode.limit} of {geocode.limit + len(geocode.refused)}")
    status = ("error: " + "; ".join(errors)) if errors else "ok"
    return venues, events, "; ".join([status, *notes])
