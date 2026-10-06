"""Merge the same concert listed by several sources (WIP-42).

Two listings match when they are on the same local day, start within MAX_GAP_MIN
minutes of each other (or one time is unknown, i.e. 00:00), have similar cleaned titles
or a shared performer, and are at the same venue or venues less than MAX_DISTANCE_M
apart. A listing joins a group only if it matches *every* member (complete linkage), so
"Earth" + "Earth + Boris" + "Boris" or a festival's acts never chain into one concert.
The same act at the same time farther apart is kept twice and reported as a conflict
(one of the two is likely mis-attributed).

Titles are similar when equal once cleaned, or when they share enough meaningful words:
genre words ("jazz", "jam session") and words found in SERIES_TITLES or more different
titles at the same venue that day (a festival or series name) do not count.

This runs before artist enrichment (no `artists` keys yet): performer names from the
sources stand in for them, so duplicates never cost extra artist lookups.

The id is a hash of venue + local date + cleaned title of the kept (best-source)
listing, so it changes when a better source appears or disappears. `aliases` holds the
ids every merged listing would have alone; the page matches hidden concerts and #c- links
on the id or any alias. Ids moved once to cleaned titles with WIP-42.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit

from .models import Concert, Venue

# Only listings on the same local calendar day are compared: a show starting at 23:30
# and one at 00:30 the next day are never merged.
MAX_GAP_MIN = 90
MAX_DISTANCE_M = 300
MIN_OVERLAP = 0.6
MAX_EXAMPLES = 5
SERIES_TITLES = 3

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
# Genre and format words: they say what kind of night it is, not who plays.
GENRE_WORDS = set(
    "jazz rock pop electro electronique techno house funk soul blues folk punk metal rap hip"
    " hop reggae dub disco rnb jam session sessions open mic boeuf blind test karaoke trio"
    " quartet quintet orchestre orchestra band big hommage tribute festival fest dj djs set"
    " club bal".split()
)
# Placeholder performer names: never evidence that two listings are the same show.
GENERIC_PERFORMERS = {
    *("variousartists artistesdivers divers invites invite guests guest specialguest".split()),
    *("specialguests tba tbc dj djs unknown inconnu".split()),
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


def _tokens(clean: str) -> set[str]:
    return {w for w in clean.split() if w not in STOPWORDS and w not in GENRE_WORDS}


def _acts(c: Concert) -> set[str]:
    keys = {_key(p) for p in c.performers}
    return {k for k in keys if len(k) >= 4 and k not in GENERIC_PERFORMERS}


def similar_titles(clean_a: str, clean_b: str, ta: set[str], tb: set[str]) -> bool:
    """Equal cleaned titles, or >= 60 % of the shorter title's meaningful words shared,
    with at least two shared words unless the shorter title is one word of >= 4 letters."""
    if clean_a and clean_a == clean_b:
        return True
    small = min(len(ta), len(tb))
    shared = ta & tb
    if not small or len(shared) / small < MIN_OVERLAP:
        return False
    return len(shared) >= 2 or (small == 1 and len(next(iter(shared))) >= 4)


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


def same_concert_across_runs(a: Concert, b: Concert, venues: dict[str, Venue]) -> bool:
    """Stricter than dedupe()'s pairwise rule, for the event store (WIP-46).

    Across runs only two listings are compared, so series words (a festival or series
    name, found over a whole day's titles) cannot be removed: "Nuits Sonores: Boris" and
    "Nuits Sonores: Earth" share enough words to look alike (review of PR #40). So the same
    slot and place are required, then a shared performer when both sides name performers,
    else equal cleaned titles; word overlap alone never matches.
    """
    if not (same_slot(a, b) and near(a, b, venues)):
        return False
    acts_a, acts_b = _acts(a), _acts(b)
    if acts_a and acts_b:
        return bool(acts_a & acts_b)
    ca = clean_title(a.title, a.performers)
    return bool(ca) and ca == clean_title(b.title, b.performers)


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
    """Merge duplicate listings; return the concerts and the counts for the report."""
    cleans = [clean_title(c.title, c.performers) for c in concerts]
    tokens = [_tokens(t) for t in cleans]
    acts = [_acts(c) for c in concerts]
    # festival / series names: words shared by SERIES_TITLES+ different titles at a venue
    titles: dict[tuple[str, str], set[frozenset[str]]] = {}
    for i, c in enumerate(concerts):
        titles.setdefault((c.venue_id, c.start[:10]), set()).add(frozenset(tokens[i]))
    series = {
        slot: {w for w in set().union(*ts) if sum(w in t for t in ts) >= SERIES_TITLES}
        for slot, ts in titles.items()
    }
    tokens = [t - series[(c.venue_id, c.start[:10])] for t, c in zip(tokens, concerts, strict=True)]

    def alike(i: int, j: int) -> bool:
        if not same_slot(concerts[i], concerts[j]):
            return False
        return bool(acts[i] & acts[j]) or similar_titles(cleans[i], cleans[j], tokens[i], tokens[j])

    def joins(i: int, members: list[int]) -> bool:
        return all(alike(i, m) and near(concerts[i], concerts[m], venues) for m in members)

    groups: list[list[int]] = []
    far_pairs: list[tuple[int, int]] = []
    group_of: dict[int, int] = {}
    by_day: dict[str, list[int]] = {}
    for i, c in enumerate(concerts):
        by_day.setdefault(c.start[:10], []).append(i)
    for idx in by_day.values():
        day_groups: list[int] = []
        for i in idx:  # an unknown-time listing too joins one group at most
            g = next((g for g in day_groups if joins(i, groups[g])), None)
            if g is None:
                g = len(groups)
                groups.append([])
                day_groups.append(g)
            groups[g].append(i)
            group_of[i] = g
        for n, i in enumerate(idx):
            for j in idx[n + 1 :]:
                a, b = concerts[i], concerts[j]
                located = a.venue_id in venues and b.venue_id in venues  # not a bare place name
                if located and not near(a, b, venues) and alike(i, j):
                    far_pairs.append((i, j))
    conflicts = sorted({tuple(sorted((group_of[i], group_of[j]))) for i, j in far_pairs})
    conflict_examples = [
        _example([concerts[groups[a][0]], concerts[groups[b][0]]]) for a, b in conflicts
    ]
    merge_examples: list[dict] = []
    out: list[Concert] = []
    seen_ids: set[str] = set()
    for members in sorted(groups, key=lambda g: concerts[g[0]].start):
        group = [concerts[i] for i in members]
        if len(group) > 1:
            merge_examples.append(_example(group))
        c = _merge(group)
        c.id = concert_id(c)
        if c.id in seen_ids:  # same title twice that day at different times
            c.id = hashlib.sha1(f"{c.id}|{c.start}".encode()).hexdigest()[:12]
        seen_ids.add(c.id)
        c.aliases = sorted({concert_id(m) for m in group} - {c.id})
        out.append(c)
    stats = {
        "merged": len(concerts) - len(groups),
        "conflicts": len(conflicts),
        "merge_examples": merge_examples[:MAX_EXAMPLES],
        "conflict_examples": conflict_examples[:MAX_EXAMPLES],
    }
    return out, stats


def _example(group: list[Concert]) -> dict:
    return {
        "date": group[0].start[:10],
        "titles": [c.title for c in group],
        "venues": [c.venue_name for c in group],
        "sources": [c.sources[0] for c in group],
    }
