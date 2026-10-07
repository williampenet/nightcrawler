"""Merge venues found by different sources into one list."""

from __future__ import annotations

import math
import re
import unicodedata

from .events import best_venue_match, place_tokens
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

    Readers give their events the configured venue name; events.attribute_venue attaches them
    to the known venue that name matches (events.best_venue_match: words in a row, exact name
    first, then the closest length). The same matching resolves the configured name here, so
    "L'Épicerie Moderne" finds the OSM "L'épicerie moderne Place René Lescot, 69320 Feyzin".

    - A match is kept as it is; only an explicit `category` in the config replaces its
      category. Its coordinates are never changed.
    - No match: a venue `config:<key>` is created instead of the `place:` fallback, with the
      configured `category`, else events_venue. events_venue is not a music category, so the
      concert rule is exactly the fallback's (events.concert_reason only uses
      `is_music_venue`; a `place:` venue has none): never stricter than before (WIP-64b).
      Coordinates: `latitude`/`longitude`, else those of the known venue that
      `coordinates_from` names (Opéra Underground plays in the "Opéra de Lyon" building, so
      the same show listed there merges within dedup's 300 m), else none (compared with
      other venues by id only).
    """
    added: list[Venue] = []
    for entry in entries:
        known = {v.id: v for v in [*venues, *added]}
        keys = {vid: place_tokens(v.name) for vid, v in known.items()}
        tokens = place_tokens(entry["venue"])
        if (match := best_venue_match(tokens, keys)) is not None:
            if "category" in entry:
                known[match].category = entry["category"]
            continue
        if not tokens:
            continue
        lat, lon = entry.get("latitude"), entry.get("longitude")
        src_id = best_venue_match(place_tokens(entry.get("coordinates_from") or ""), keys)
        src = known.get(src_id) if src_id else None
        if lat is None and src is not None and src.latitude is not None:
            lat, lon = src.latitude, src.longitude
        category = entry.get("category", "events_venue")
        key = "".join(tokens)
        added.append(Venue(f"config:{key}", entry["venue"], lat, lon, category, sources=["config"]))
    return added
