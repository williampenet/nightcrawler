"""Merge the same concert listed by several sources (WIP-42).

Two listings are one concert when they are on the same local day, start within
MAX_GAP_MIN minutes of each other (or one time is unknown, i.e. 00:00), have similar
cleaned titles or a shared performer, and are at the same venue or venues less than
MAX_DISTANCE_M apart. The same act at the same time farther apart is kept twice and
reported as a conflict (one of the two is likely mis-attributed).

This runs before artist enrichment (no `artists` keys yet): performer names from the
sources stand in for them, so duplicates never cost extra artist lookups.

The id is a hash of venue + local date + cleaned title of the kept listing: it does not
depend on which source was read first, so it stays stable when a source comes or goes.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit

from .models import Concert, Venue

MAX_GAP_MIN = 90
MAX_DISTANCE_M = 300
MIN_OVERLAP = 0.6
MAX_EXAMPLES = 5

NOISE_RE = re.compile(  # applied to lower-case text without accents
    r"\b(?:1\s*e?re|premiere|first)\s+partie\b|\+\s*guests?\b|\bcomplet\b"
    r"|\bsold[- ]?out\b|\bannulee?s?\b|\brelease party\b",
)
SEGMENT_RE = re.compile(r"\s+[-–—]\s+|\s*:\s*")
TOUR_RE = re.compile(r"\b(?:tour|tournee|world|anniversary|album|19\d\d|20\d\d)\b")
GENERIC = {"concert", "concerts", "live", "showcase", "soiree", "presente", "presents"}
STOPWORDS = GENERIC | {
    *("the le la les l de des du d et and a en x feat ft with avec w".split()),
    *("night party invite invites guest guests".split()),
}
# Which listing's title wins: the venue's own site, then agendas, platforms, ticketing.
SOURCE_RANK = (("gancio:", 1), ("platform:", 2), ("ticketmaster", 3))


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text) if w]


def _key(name: str) -> str:
    return "".join(_words(_ascii(name)))


def clean_title(title: str, performers: list[str] | tuple[str, ...] = ()) -> str:
    """Comparable title: lower case, no accents, no noise, repeated name and tour dropped."""
    text = NOISE_RE.sub(" ", _ascii(title))
    segments: list[str] = []
    for seg in SEGMENT_RE.split(text):
        words = " ".join(_words(seg))
        if words and words not in segments and not set(words.split()) <= GENERIC:
            segments.append(words)
    acts = {_key(p) for p in performers}
    if len(segments) > 1 and segments[0].replace(" ", "") in acts:
        segments = segments[:1]  # "Act - Tour name": the act is enough
    segments = [s for i, s in enumerate(segments) if i == 0 or not TOUR_RE.search(s)]
    return " ".join(segments)


def source_rank(source: str) -> int:
    return next((r for prefix, r in SOURCE_RANK if source.startswith(prefix)), 0)


def _tokens(c: Concert) -> set[str]:
    return {w for w in clean_title(c.title, c.performers).split() if w not in STOPWORDS}


def similar(a: Concert, b: Concert) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    small = min(len(ta), len(tb))
    if small and len(ta & tb) / small >= MIN_OVERLAP:
        return True
    pa = {k for p in a.performers if len(k := _key(p)) >= 4}
    return bool(pa & {k for p in b.performers if len(k := _key(p)) >= 4})


def make_links(url: str | None, ticket_url: str | None, source: str) -> list[dict[str, str]]:
    """Labelled links of one listing: its page, then its ticket page if different."""
    page = "Billets" if source_rank(source) >= 2 else "Page"  # a ticketing page sells tickets
    pairs = [(page, url), ("Billets", ticket_url)]
    links: list[dict[str, str]] = []
    for label, u in pairs:
        if u and u not in [x["url"] for x in links]:
            links.append({"label": label, "url": u})
    return links


def _start(c: Concert) -> datetime:
    return datetime.fromisoformat(c.start)


def _known(d: datetime) -> bool:
    return (d.hour, d.minute) != (0, 0)


def same_slot(a: Concert, b: Concert) -> bool:
    da, db = _start(a), _start(b)
    if da.date() != db.date():
        return False
    return not (_known(da) and _known(db)) or abs((da - db).total_seconds()) <= MAX_GAP_MIN * 60


def distance_m(a: Venue, b: Venue) -> float:
    la1, la2 = math.radians(a.latitude), math.radians(b.latitude)
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b.longitude - a.longitude) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def near(a: Concert, b: Concert, venues: dict[str, Venue]) -> bool:
    if a.venue_id == b.venue_id:
        return True
    va, vb = venues.get(a.venue_id), venues.get(b.venue_id)
    return va is not None and vb is not None and distance_m(va, vb) <= MAX_DISTANCE_M


def concert_id(c: Concert) -> str:
    key = clean_title(c.title, c.performers).replace(" ", "") or _key(c.title)
    raw = f"{c.venue_id}|{_start(c).date().isoformat()}|{key}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _merge(group: list[Concert]) -> Concert:
    group = sorted(group, key=lambda c: source_rank(c.sources[0]))  # stable: first seen wins ties
    best = group[0]
    known = [c for c in group if _known(_start(c))]
    start = min((c.start for c in known), key=lambda s: datetime.fromisoformat(s), default=None)
    performers: list[str] = []
    for c in group:
        performers += [p for p in c.performers if _key(p) not in map(_key, performers)]
    links, urls, labels = [], set(), set()
    for c in group:
        for link in c.links:
            if link["url"] in urls:
                continue
            label = link["label"] if link["label"] not in labels else urlsplit(link["url"]).netloc
            urls.add(link["url"])
            labels.add(label)
            links.append(link | {"label": label})
    return Concert(
        id=best.id,
        title=best.title,
        start=start or best.start,
        venue_id=best.venue_id,
        venue_name=best.venue_name,
        url=best.url or next((c.url for c in group if c.url), None),
        ticket_url=best.ticket_url or next((c.ticket_url for c in group if c.ticket_url), None),
        performers=performers,
        sources=list(dict.fromkeys(s for c in group for s in c.sources)),
        reason=best.reason,
        links=links,
    )


def dedupe(concerts: list[Concert], venues: dict[str, Venue]) -> tuple[list[Concert], dict]:
    """Merge duplicate listings; return the concerts and {merged, conflicts, examples}."""
    parent = list(range(len(concerts)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_day: dict[str, list[int]] = {}
    for i, c in enumerate(concerts):
        by_day.setdefault(c.start[:10], []).append(i)
    far_pairs: list[tuple[int, int]] = []
    for idx in by_day.values():
        for n, i in enumerate(idx):
            for j in idx[n + 1 :]:
                a, b = concerts[i], concerts[j]
                if not (same_slot(a, b) and similar(a, b)):
                    continue
                if near(a, b, venues):
                    parent[root(j)] = root(i)
                else:
                    far_pairs.append((i, j))
    groups: dict[int, list[Concert]] = {}
    for i, c in enumerate(concerts):
        groups.setdefault(root(i), []).append(c)
    examples: list[dict] = []
    conflicts = {tuple(sorted((root(i), root(j)))) for i, j in far_pairs if root(i) != root(j)}
    for gi, gj in sorted(conflicts):
        a, b = concerts[gi], concerts[gj]
        examples.append(_example("conflict", [a, b]))
    out: list[Concert] = []
    seen_ids: set[str] = set()
    for group in sorted(groups.values(), key=lambda g: g[0].start):
        if len(group) > 1:
            examples.append(_example("merged", group))
        c = _merge(group)
        c.id = concert_id(c)
        if c.id in seen_ids:  # same title twice that day at different times
            c.id = hashlib.sha1(f"{c.id}|{c.start}".encode()).hexdigest()[:12]
        seen_ids.add(c.id)
        out.append(c)
    stats = {
        "merged": len(concerts) - len(groups),
        "conflicts": len(conflicts),
        "examples": examples[:MAX_EXAMPLES],
    }
    return out, stats


def _example(kind: str, group: list[Concert]) -> dict:
    return {
        "kind": kind,
        "date": group[0].start[:10],
        "titles": [c.title for c in group],
        "venues": [c.venue_name for c in group],
        "sources": [c.sources[0] for c in group],
    }
