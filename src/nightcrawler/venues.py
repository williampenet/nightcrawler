"""Merge venues found by different sources into one list."""

from __future__ import annotations

import math
import re
import unicodedata

from .models import Venue

SAME_PLACE_METERS = 150


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
    if distance_m(a, b) > SAME_PLACE_METERS:
        return False
    na, nb = _norm(a.name), _norm(b.name)
    return bool(na and nb) and (na in nb or nb in na)


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
