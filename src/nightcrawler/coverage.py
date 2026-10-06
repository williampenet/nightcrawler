"""Coverage of the reference set (PRD FR-1 gate, FR-11, WIP-55).

A reference event (eval/reference/watch_events.csv) is found when one concert of the run
has the same local date, the same venue (normalised words, or an alias) and at least one of
the event's artists, as whole words, in its title or performers. A venue-only match does
not count. Aliases and the words ignored in artist names live in
eval/reference/matching.yaml, never in this code. Deterministic: no network, no model.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .models import Concert

ARTIST_SEP = re.compile(r"[+,&/]")
# words that do not tell venues apart (same spirit as venues._norm)
VENUE_STOP = {"le", "la", "les", "l", "de", "du", "des", "d", "the", "salle", "club", "bar"}
MIN_ARTIST_CHARS = 3  # shorter pieces ("PΞB" -> "pb") would match by chance


@dataclass(frozen=True)
class Matching:
    """matching.yaml: venue aliases, and words dropped at the ends of an artist piece."""

    venue_aliases: dict[str, list[str]] = field(default_factory=dict)
    leading_articles: frozenset[str] = frozenset()
    generic_words: frozenset[str] = frozenset()


def words(text: str) -> str:
    """Lower-case ASCII words separated by single spaces."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def venue_words(name: str) -> frozenset[str]:
    return frozenset(w for w in words(name).split() if w not in VENUE_STOP)


def _trim(piece: str, m: Matching) -> str:
    """Drops a leading article, then generic words at either end, while other words remain:
    "The Lemon Twigs" -> "lemon twigs", "Quatuor Béla" -> "bela", "Trio" stays "trio"."""
    w = piece.split()
    if len(w) > 1 and w[0] in m.leading_articles:
        w = w[1:]
    while len(w) > 1 and w[0] in m.generic_words:
        w = w[1:]
    while len(w) > 1 and w[-1] in m.generic_words:
        w = w[:-1]
    return " ".join(w)


def artist_pieces(artists: str, m: Matching | None = None) -> list[str]:
    """The row's names, split on + , & /. Show titles and descriptors written by the watch
    ("Philippe Katerine symphonique, Aux anges", "Tigran Hamasyan, Manifest") become pieces
    too: a show title may match a concert titled after the show, and a descriptor glued to
    a name ("… symphonique") stops that piece from matching; another piece of the row
    usually still does. Kept as is: guessing which words are a show title would not be
    deterministic data."""
    m = m or Matching()
    out = [_trim(words(p), m) for p in ARTIST_SEP.split(artists)]
    return [p for p in dict.fromkeys(out) if len(p.replace(" ", "")) >= MIN_ARTIST_CHARS]


def load_reference(path: str | Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return [
            {"date": r["date"].strip(), "artists": r["artists"], "venue": r["venue"]}
            for r in csv.DictReader(fh)
        ]


def load_matching(path: str | Path) -> Matching:
    p = Path(path)
    data = (yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None) or {}
    aliases = data.get("venue_aliases") or {}
    ignore = data.get("artist_words") or {}
    return Matching(
        venue_aliases={str(k): [str(v) for v in vs or []] for k, vs in aliases.items()},
        leading_articles=frozenset(words(str(x)) for x in ignore.get("leading_articles") or []),
        generic_words=frozenset(words(str(x)) for x in ignore.get("generic") or []),
    )


def same_venue(ref_venue: str, concert_venue: str, m: Matching) -> bool:
    have = venue_words(concert_venue)
    names = [ref_venue, *m.venue_aliases.get(ref_venue, [])]
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
    matching: Matching | None = None,
) -> dict:
    """{in_window, found, rate, date_venue_only, per_venue: {venue: [in_window, found]},
    events: [{row, found, concert_id}]}.

    Window: the run keeps concerts from the start of today to now + window_days
    (events.in_window). Reference rows only have a date, so the partial last day is left
    out: rows dated from today up to, not including, (now + window_days).date().
    date_venue_only: rows with a concert at that date and venue but no artist match.
    """
    m = matching or Matching()
    first = now.astimezone(tz).date().isoformat()
    end = (now + timedelta(days=window_days)).astimezone(tz).date().isoformat()
    by_date: dict[str, list[Concert]] = {}
    for c in concerts:
        day = datetime.fromisoformat(c.start).astimezone(tz).date().isoformat()
        by_date.setdefault(day, []).append(c)
    per_venue: dict[str, list[int]] = {}
    events = []
    venue_only = 0
    for row, ref in enumerate(reference):
        if not first <= ref["date"] < end:
            continue
        pieces = artist_pieces(ref["artists"], m)
        here = [
            c for c in by_date.get(ref["date"], []) if same_venue(ref["venue"], c.venue_name, m)
        ]
        hit = next((c for c in here if has_artist(pieces, c)), None)
        venue_only += bool(here) and hit is None
        counts = per_venue.setdefault(ref["venue"], [0, 0])
        counts[0] += 1
        counts[1] += hit is not None
        events.append({"row": row, "found": hit is not None, "concert_id": hit.id if hit else None})
    n, found = len(events), sum(e["found"] for e in events)
    return {
        "in_window": n,
        "found": found,
        "rate": round(found / n, 3) if n else None,
        "date_venue_only": venue_only,
        "per_venue": dict(sorted(per_venue.items())),
        "events": events,  # row: 0-based index of the row in watch_events.csv
    }
