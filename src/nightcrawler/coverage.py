"""Coverage of the reference set (PRD FR-1 gate, FR-11, WIP-55).

A reference event (eval/reference/watch_events.csv) is found when one concert of the run
has the same local date, the same venue (normalised words, or an alias from
venue_aliases.yaml) and at least one of the event's artists, as whole words, in its title
or performers. A venue-only match does not count. Deterministic: no network, no model.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .models import Concert

ARTIST_SEP = re.compile(r"[+,&/]")
# words that do not tell venues apart (same spirit as venues._norm)
VENUE_STOP = {"le", "la", "les", "l", "de", "du", "des", "d", "the", "salle", "club", "bar"}
MIN_ARTIST_CHARS = 3  # shorter pieces ("PΞB" -> "pb") would match by chance


def words(text: str) -> str:
    """Lower-case ASCII words separated by single spaces."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def venue_words(name: str) -> frozenset[str]:
    return frozenset(w for w in words(name).split() if w not in VENUE_STOP)


def artist_pieces(artists: str) -> list[str]:
    out = [words(p) for p in ARTIST_SEP.split(artists)]
    return [p for p in dict.fromkeys(out) if len(p.replace(" ", "")) >= MIN_ARTIST_CHARS]


def load_reference(path: str | Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return [
            {"date": r["date"].strip(), "artists": r["artists"], "venue": r["venue"]}
            for r in csv.DictReader(fh)
        ]


def load_aliases(path: str | Path) -> dict[str, list[str]]:
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None
    return {str(k): [str(v) for v in vs or []] for k, vs in (data or {}).items()}


def same_venue(ref_venue: str, concert_venue: str, aliases: dict[str, list[str]]) -> bool:
    have = venue_words(concert_venue)
    names = [ref_venue, *aliases.get(ref_venue, [])]
    return any((need := venue_words(n)) and need <= have for n in names)


def has_artist(pieces: list[str], concert: Concert) -> bool:
    text = " " + " ".join(words(t) for t in [concert.title, *concert.performers]) + " "
    return any(f" {p} " in text for p in pieces)


def measure(
    reference: list[dict[str, str]],
    concerts: list[Concert],
    now: datetime,
    window_days: int,
    tz: ZoneInfo,
    aliases: dict[str, list[str]] | None = None,
) -> dict:
    """{in_window, found, rate, per_venue: {venue: [in_window, found]}, events: [...]}.
    The window is the run's: today through now + window_days, local dates."""
    aliases = aliases or {}
    first = now.astimezone(tz).date().isoformat()
    last = (now + timedelta(days=window_days)).astimezone(tz).date().isoformat()
    by_date: dict[str, list[Concert]] = {}
    for c in concerts:
        day = datetime.fromisoformat(c.start).astimezone(tz).date().isoformat()
        by_date.setdefault(day, []).append(c)
    per_venue: dict[str, list[int]] = {}
    events = []
    for ref in reference:
        if not first <= ref["date"] <= last:
            continue
        pieces = artist_pieces(ref["artists"])
        hit = next(
            (
                c
                for c in by_date.get(ref["date"], [])
                if same_venue(ref["venue"], c.venue_name, aliases) and has_artist(pieces, c)
            ),
            None,
        )
        counts = per_venue.setdefault(ref["venue"], [0, 0])
        counts[0] += 1
        counts[1] += hit is not None
        events.append(ref | {"concert_id": hit.id if hit else None})
    n, found = len(events), sum(e["concert_id"] is not None for e in events)
    return {
        "in_window": n,
        "found": found,
        "rate": round(found / n, 3) if n else None,
        "per_venue": dict(sorted(per_venue.items())),
        "events": events,  # public events (the reference file is public)
    }
