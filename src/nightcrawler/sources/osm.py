"""Venue discovery from OpenStreetMap through the Overpass API (data © OpenStreetMap, ODbL)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from ..config import Zone
from ..http import PROJECT_URL, Fetcher
from ..models import Venue
from ..venues import distance_m

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_MIRRORS = (
    OVERPASS_URL,
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
RETRY_STATUSES = {429, 500, 502, 503, 504}
# Overpass anti-abuse rules reject anonymous-looking clients with HTTP 406:
# send an explicit Accept and a Referer pointing to the project.
OVERPASS_HEADERS = {"Accept": "application/json", "Referer": PROJECT_URL + "/"}
OVERPASS_TIMEOUT = 150.0  # the query itself allows 120 s server-side

AMENITIES = (
    "music_venue",
    "concert_hall",
    "nightclub",
    "arts_centre",
    "theatre",
    "events_venue",
    "social_centre",
    "community_centre",
)


def build_query(zone: Zone) -> str:
    around = f"around:{int(zone.radius_km * 1000)},{zone.latitude},{zone.longitude}"
    amenity_re = "|".join(AMENITIES)
    return (
        "[out:json][timeout:120];\n"
        "(\n"
        f'  nwr["amenity"~"^({amenity_re})$"]({around});\n'
        f'  nwr["live_music"="yes"]({around});\n'
        ");\n"
        "out center tags;\n"
    )


def category_of(tags: dict[str, str]) -> str:
    amenity = tags.get("amenity", "")
    if amenity in AMENITIES:
        return amenity
    if tags.get("live_music") == "yes":
        return "live_music"
    return amenity or "other"


def website_of(tags: dict[str, str]) -> str | None:
    for key in ("website", "contact:website", "url", "operator:website"):
        value = tags.get(key, "").strip()
        if value:
            value = value.split(";")[0].strip()
            if not value.startswith(("http://", "https://")):
                value = "https://" + value
            return value
    return None


def address_of(tags: dict[str, str]) -> str | None:
    street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
    city = " ".join(p for p in (tags.get("addr:postcode"), tags.get("addr:city")) if p)
    joined = ", ".join(p for p in (street, city) if p)
    return joined or None


def parse(payload: dict) -> list[Venue]:
    venues: list[Venue] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        venues.append(
            Venue(
                id=f"osm:{el['type']}/{el['id']}",
                name=name,
                latitude=float(lat),
                longitude=float(lon),
                category=category_of(tags),
                website=website_of(tags),
                address=address_of(tags),
                sources=["openstreetmap"],
            )
        )
    return venues


def osmium_filter_expressions() -> list[str]:
    """Same selection as the Overpass query, in osmium tags-filter syntax."""
    return ["nwr/amenity=" + ",".join(AMENITIES), "nwr/live_music=yes"]


def _centre(geometry: dict) -> tuple[float, float] | None:
    """Rough centre (lon, lat) of a GeoJSON geometry: mean of its first ring's points."""
    coords = geometry.get("coordinates")
    kind = geometry.get("type")
    if kind == "Point":
        return coords[0], coords[1]
    ring = {
        "LineString": lambda c: c,
        "Polygon": lambda c: c[0],
        "MultiPolygon": lambda c: c[0][0],
    }.get(kind)
    if ring is None or not coords:
        return None
    pts = ring(coords)
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def parse_geojsonseq(path: str | Path, zone: Zone) -> list[Venue]:
    """Read an `osmium export -f geojsonseq --add-unique-id=type_id` file, keep the radius."""
    centre = Venue("", "", zone.latitude, zone.longitude, "")
    kinds = {"n": "node", "w": "way", "r": "relation"}
    venues: list[Venue] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("\x1e")
        if not line:
            continue
        feat = json.loads(line)
        tags = feat.get("properties") or {}
        name = tags.get("name")
        point = _centre(feat.get("geometry") or {})
        if not name or point is None:
            continue
        uid = str(feat.get("id", ""))
        v = Venue(
            id=f"osm:{kinds.get(uid[:1], 'node')}/{uid[1:]}",
            name=name,
            latitude=point[1],
            longitude=point[0],
            category=category_of(tags),
            website=website_of(tags),
            address=address_of(tags),
            sources=["openstreetmap"],
        )
        if distance_m(centre, v) <= zone.radius_km * 1000:
            venues.append(v)
    # an area and its nodes can both carry the tags: keep one per name and place
    unique: dict[tuple[str, int, int], Venue] = {}
    for v in venues:
        unique.setdefault((v.name, round(v.latitude, 3), round(v.longitude, 3)), v)
    return list(unique.values())


def discover(
    zone: Zone,
    fetcher: Fetcher,
    mirrors=OVERPASS_MIRRORS,
    backoff: float = 10.0,
    extract: str | Path | None = None,
) -> list[Venue]:
    """Venues from a local extract when available, else Overpass (retries, then mirrors)."""
    if extract and Path(extract).exists():
        venues = parse_geojsonseq(extract, zone)
        log.info("OpenStreetMap: %d venues (local extract)", len(venues))
        return venues
    query = build_query(zone)
    errors: list[str] = []
    for url in mirrors:
        for attempt in range(2):
            try:
                resp = fetcher.post(
                    url, data={"data": query}, headers=OVERPASS_HEADERS, timeout=OVERPASS_TIMEOUT
                )
            except httpx.HTTPError as exc:
                errors.append(f"{url}: {type(exc).__name__}")
                break
            if resp.status == 200:
                try:
                    venues = parse(json.loads(resp.text))
                except ValueError:
                    errors.append(f"{url}: invalid JSON")
                    break
                log.info("OpenStreetMap: %d venues (%s)", len(venues), url)
                return venues
            errors.append(f"{url}: HTTP {resp.status}")
            if resp.status not in RETRY_STATUSES:
                break
            time.sleep(backoff * (attempt + 1))
    raise RuntimeError("Overpass unavailable: " + "; ".join(errors))
