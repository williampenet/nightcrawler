"""Venue discovery from OpenStreetMap through the Overpass API (data © OpenStreetMap, ODbL)."""

from __future__ import annotations

import json
import logging

from ..config import Zone
from ..http import Fetcher
from ..models import Venue

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

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


def discover(zone: Zone, fetcher: Fetcher) -> list[Venue]:
    resp = fetcher.post(OVERPASS_URL, data={"data": build_query(zone)})
    if resp.status != 200:
        raise RuntimeError(f"Overpass returned HTTP {resp.status}")
    venues = parse(json.loads(resp.text))
    log.info("OpenStreetMap: %d venues", len(venues))
    return venues
