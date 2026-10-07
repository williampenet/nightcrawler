"""Merge venues found by different sources into one list."""

from __future__ import annotations

import math
import re
import unicodedata

from .events import place_key
from .models import Venue

SAME_PLACE_METERS = 150
# Identical names from different sources merge further apart: Ticketmaster and OpenStreetMap
# place "Le Transbordeur" ~590 m apart (2026-10-06 run: 45.778753,4.859536 vs
# 45.78397,4.86088, distance_m), which split every concert there into two (WIP-51).
SAME_NAME_METERS = 1500
MIN_SAME_NAME_LEN = 5  # very short normalised names are too ambiguous to merge far apart
# Generic names shared by different buildings (one per town): never merged beyond 150 m
GENERIC = re.compile(r"(desfetes|polyvalente|municipale|communale|mairie|eglise)")


def distance_m(a: Venue, b: Venue) -> float:
    r = 6_371_000
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dp = p2 - p1
    dl = math.radians(b.longitude - a.longitude)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _norm(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"\b(le|la|les|l|the|salle|club|bar|theatre)\b", " ", name)
    return re.sub(r"[^a-z0-9]+", "", name)


def same_venue(a: Venue, b: Venue, *, cross_source: bool = True) -> bool:
    """Same place: names contain each other within 150 m, or (between two different
    sources only) identical, specific names within 1.5 km. Two features of one source
    with the same name are distinct objects by construction."""
    d = distance_m(a, b)
    na, nb = _norm(a.name), _norm(b.name)
    if not (na and nb) or d > SAME_NAME_METERS:
        return False
    if d <= SAME_PLACE_METERS:
        return na in nb or nb in na
    return cross_source and na == nb and len(na) >= MIN_SAME_NAME_LEN and not GENERIC.search(na)


def is_excluded(v: Venue, excluded: tuple[str, ...]) -> bool:
    """True when the venue's normalised name equals an excluded name (config/zone.yaml)."""
    nv = _norm(v.name)
    return bool(nv) and any(nv == _norm(x) for x in excluded)


def merge(groups: list[list[Venue]]) -> tuple[list[Venue], dict[str, str]]:
    """Merge venue lists. Returns the merged venues and an alias map (old id -> kept id)."""
    merged: list[Venue] = []
    group_of: dict[str, int] = {}  # merged venue id -> index of the group it came from
    alias: dict[str, str] = {}
    for gi, group in enumerate(groups):
        for v in group:
            match = next(
                (m for m in merged if same_venue(m, v, cross_source=group_of[m.id] != gi)),
                None,
            )
            if match is None:
                merged.append(v)
                group_of[v.id] = gi
                alias[v.id] = v.id
                continue
            alias[v.id] = match.id
            match.website = match.website or v.website
            match.address = match.address or v.address
            for s in v.sources:
                if s not in match.sources:
                    match.sources.append(s)
    return merged, alias


def configured_venues(entries: tuple[dict, ...], venues: list[Venue]) -> list[Venue]:
    """Known venues for "Mes salles" (config `priority_venues`, WIP-64); returns those to add.

    Readers give their events the configured venue name, and events.attribute_venue attaches
    them to the known venue of that name. A known venue with the same place key (the exact
    match attribute_venue prefers) takes the configured `category`, if any. Without one, a
    venue `config:<key>` is created rather than leaving the events to a `place:` venue with no
    category: configured category (default events_venue, which gives no music rule) and
    coordinates: `latitude`/`longitude`, else those of the known venue named in
    `coordinates_from` (Opéra Underground plays in the "Opéra de Lyon" building, so the same
    show listed there merges within dedup's 300 m), else none: the venue is then compared
    with other venues by id only.
    """
    added: list[Venue] = []
    for entry in entries:
        key = place_key(entry["venue"])
        same = [v for v in [*venues, *added] if key and place_key(v.name) == key]
        for v in same:
            v.category = entry.get("category", v.category)
        if key and not same:
            lat, lon = entry.get("latitude"), entry.get("longitude")
            src_key = place_key(entry.get("coordinates_from") or "")
            src = next((v for v in venues if src_key and place_key(v.name) == src_key), None)
            if lat is None and src is not None and src.latitude is not None:
                lat, lon = src.latitude, src.longitude
            category = entry.get("category", "events_venue")
            added.append(
                Venue(f"config:{key}", entry["venue"], lat, lon, category, sources=["config"])
            )
    return added
