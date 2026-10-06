"""Merge venues found by different sources into one list."""

from __future__ import annotations

import math
import re
import unicodedata

from .models import Venue

SAME_PLACE_METERS = 150
# Identical names merge further apart: Ticketmaster and OpenStreetMap place "Le Transbordeur"
# 580 m apart (measured on the 2026-10-06 run: 45.778753,4.859536 vs 45.78397,4.86088),
# which split every concert there into two (WIP-51).
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


def same_venue(a: Venue, b: Venue) -> bool:
    d = distance_m(a, b)
    na, nb = _norm(a.name), _norm(b.name)
    if not (na and nb) or d > SAME_NAME_METERS:
        return False
    if d <= SAME_PLACE_METERS:
        return na in nb or nb in na
    return na == nb and len(na) >= MIN_SAME_NAME_LEN and not GENERIC.search(na)


def is_excluded(v: Venue, excluded: tuple[str, ...]) -> bool:
    """True when the venue's normalised name contains an excluded name (config/zone.yaml)."""
    nv = _norm(v.name)
    return any(e and e in nv for e in (_norm(x) for x in excluded))


def merge(groups: list[list[Venue]]) -> tuple[list[Venue], dict[str, str]]:
    """Merge venue lists. Returns the merged venues and an alias map (old id -> kept id)."""
    merged: list[Venue] = []
    alias: dict[str, str] = {}
    for group in groups:
        for v in group:
            match = next((m for m in merged if same_venue(m, v)), None)
            if match is None:
                merged.append(v)
                alias[v.id] = v.id
                continue
            alias[v.id] = match.id
            match.website = match.website or v.website
            match.address = match.address or v.address
            for s in v.sources:
                if s not in match.sources:
                    match.sources.append(s)
    return merged, alias
