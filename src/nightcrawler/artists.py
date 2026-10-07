"""Find who plays each concert and enrich them with public data (ADR-0002).

- performers: every act of the line-up (lineup.py, WIP-72), else split from the title
- Deezer: exact normalised name match only → id, fans, related artists (co-listening signal)
- MusicBrainz: style tags for the same name (CC BY-NC-SA, attributed on the page)

Exact names are not enough: "Asna" or "Sheldon" have homonyms, and a homonym's related
artists produced absurd matches (WIP-40). Related artists and tags are only kept for
*confident* identities: one exact-name artist on Deezer and exactly one on MusicBrainz, a
name of at least MIN_KEY_LEN characters, and enough fans for Deezer's related list to mean
something. Very well-known artists (FAMOUS_FANS) may have a short name ("Air") and, when
MusicBrainz lists homonyms, keep their Deezer related artists but no tags.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from .http import Fetcher
from .lineup import parse_title
from .models import Concert

log = logging.getLogger(__name__)

DEEZER_SEARCH = "https://api.deezer.com/search/artist"
DEEZER_RELATED = "https://api.deezer.com/artist/{id}/related"
MB_SEARCH = "https://musicbrainz.org/ws/2/artist"
MAX_PERFORMERS = 6
MAX_RELATED = 20
MAX_TAGS = 8
MAX_LOOKUPS = 400  # cold-cache cap: ~400 names stays well inside the 45 min job
MIN_FANS = 1000  # below this, Deezer's related list is thin and homonyms are likely
MIN_KEY_LEN = 4
FAMOUS_FANS = 50_000  # well-known enough that a short or MusicBrainz-ambiguous name is safe

SPLIT_RE = re.compile(r"\s+(?:\+|/|\||x|×|w/|feat\.?|ft\.?)\s+|\s*,\s*", re.IGNORECASE)
PREFIX_RE = re.compile(
    r"^\s*(?:concert|live|showcase|release party|soir[ée]e)\s*[:\-–—]\s*", re.IGNORECASE
)
NOISE_RE = re.compile(
    r"\((?:complet|sold out|annul[ée]|report[ée]|gratuit|free)[^)]*\)|\[[^\]]*\]", re.IGNORECASE
)


def norm(name: str) -> str:
    """Normalised key: lower case, no accents, alphanumerics only."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _clean(names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        n = " ".join(str(n).split()).strip(" -–—:")
        key = norm(n)
        if 2 <= len(n) <= 60 and key and key not in seen:
            seen.add(key)
            out.append(n)
    return out[:MAX_PERFORMERS]


def performers_of(concert: Concert) -> tuple[list[str], str | None]:
    """Candidate performer names, plus the whole cleaned title when splitting it.

    The whole title is tried first by the caller: "Earth, Wind & Fire" must not
    become "Earth" (a different, real band).
    """
    if concert.performers:
        return _clean(concert.performers), None
    title = " ".join(NOISE_RE.sub(" ", PREFIX_RE.sub("", concert.title)).split())
    parts: list[str] = []
    # "Event name: artists" or "Artist: tour name" — keep both sides, exact matching sorts it out
    for side in title.split(":", 1):
        parts.extend(SPLIT_RE.split(side))
    names = _clean(parts)
    whole = title if len(names) > 1 or ":" in title else None
    return names, whole


@dataclass
class Artist:
    key: str
    name: str
    deezer_id: int | None = None
    fans: int | None = None
    related: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confident: bool = False
    doubt: str | None = None  # why not confident: ambiguous | low_fans | short_name

    @property
    def identified(self) -> bool:
        return self.deezer_id is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _dicts(value: Any) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _get_json(fetcher: Fetcher, url: str, params: dict) -> dict | None:
    try:
        resp = fetcher.get(url, params=params, check_robots=False)  # public APIs, not crawling
    except httpx.HTTPError as exc:
        log.warning("artist lookup failed: %s", type(exc).__name__)
        return None
    if resp.status != 200:
        return None
    try:
        data = json.loads(resp.text)
    except ValueError:
        return None
    if not isinstance(data, dict) or "error" in data:
        # Deezer reports quota errors with HTTP 200: do not keep them in the cache
        fetcher.forget(url, params)
        return None
    return data


def deezer_search(fetcher: Fetcher, name: str) -> tuple[int, int, int] | None:
    """(id, fans, number of exact-name artists) for the best-known exact-name match."""
    data = _get_json(fetcher, DEEZER_SEARCH, {"q": name, "limit": "10"})
    if not data:
        return None
    exact = [
        a
        for a in _dicts(data.get("data"))
        if norm(str(a.get("name") or "")) == norm(name) and _int(a.get("id"))
    ]
    if not exact:
        return None
    match = max(exact, key=lambda a: _int(a.get("nb_fan")))
    return match["id"], _int(match.get("nb_fan")), len(exact)


def deezer_related(fetcher: Fetcher, artist_id: int) -> list[str]:
    related = _get_json(fetcher, DEEZER_RELATED.format(id=artist_id), {"limit": str(MAX_RELATED)})
    names = [str(a["name"]) for a in _dicts((related or {}).get("data")) if a.get("name")]
    return names[:MAX_RELATED]


def musicbrainz_tags(fetcher: Fetcher, name: str) -> list[str] | None:
    """Tags of the single exact-name artist; None when there is none or several."""
    query = 'artist:"' + name.replace('"', " ") + '"'
    data = _get_json(fetcher, MB_SEARCH, {"query": query, "fmt": "json", "limit": "5"})
    exact = [
        a
        for a in _dicts((data or {}).get("artists"))
        if a.get("score") == 100 and norm(str(a.get("name") or "")) == norm(name)
    ]
    if len(exact) != 1:
        return None
    tags = sorted(_dicts(exact[0].get("tags")), key=lambda t: -_int(t.get("count")))
    return [str(t["name"]).lower() for t in tags if t.get("name")][:MAX_TAGS]


def _lookup(fetcher: Fetcher, name: str) -> Artist:
    a = Artist(key=norm(name), name=name)
    try:
        found = deezer_search(fetcher, name)
        if found:
            a.deezer_id, a.fans, homonyms = found
            famous = a.fans >= FAMOUS_FANS
            if homonyms > 1:
                a.doubt = "ambiguous"
            elif len(a.key) < MIN_KEY_LEN and not famous:
                a.doubt = "short_name"
            elif a.fans < MIN_FANS:
                a.doubt = "low_fans"
            else:
                tags = musicbrainz_tags(fetcher, name)
                if tags is None and not famous:
                    a.doubt = "unverified"  # not exactly one MusicBrainz artist of that name
                else:
                    a.tags, a.related = tags or [], deezer_related(fetcher, a.deezer_id)
                    a.confident = True
    except Exception as exc:  # one odd payload must never stop the run
        log.warning("artist lookup crashed: %s", type(exc).__name__)
        a.deezer_id = None
    return a


def _join(acts: list[str], parts: list[str], whole: str) -> list[str]:
    """`acts` with `parts` (all present) replaced by `whole` at the first part's place."""
    keys = {norm(p) for p in parts}
    first = next(i for i, a in enumerate(acts) if norm(a) in keys)
    rest = [a for a in acts if norm(a) not in keys]
    return rest[:first] + [whole] + rest[first:]


def _lineup_artists(c: Concert, get: Callable[[str], Artist | None]) -> list[str]:
    """Artist keys of a line-up of two acts or more; may join split parts back in c.lineup.

    When the line-up was split from the title (no source performers), the whole title and
    then each "A & B" / "A, B" part are tried first: a known artist of that exact name
    ("Earth, Wind & Fire", "Simon & Garfunkel") is one act, not several (lineup.py).
    """
    acts = list(c.lineup)
    if not c.performers:
        whole, groups = parse_title(c.title)
        flat = [a for g in groups for a in g]
        candidates = [(whole, flat)] + [
            (_group_text(whole, g) or " & ".join(g), g) for g in groups if len(g) > 1
        ]
        for i, (name, parts) in enumerate(candidates):
            present = {norm(a) for a in acts}
            if len(parts) < 2 or not all(norm(p) in present for p in parts):
                continue
            if (a := get(name)) and a.identified:
                acts = _join(acts, parts, name)
                if i == 0:
                    break  # the whole title is one artist
        c.lineup = acts
    keys: list[str] = []
    for name in acts[:MAX_PERFORMERS]:
        a = get(name)
        if a and a.identified and a.key not in keys:
            keys.append(a.key)
    return keys


def _group_text(whole: str, parts: list[str]) -> str | None:
    """The stretch of `whole` from the first part to the last one ("Simon & Garfunkel")."""
    start, end = whole.find(parts[0]), whole.rfind(parts[-1])
    if start < 0 or end < start:
        return None
    return whole[start : end + len(parts[-1])]


def enrich(
    concerts: list[Concert], fetcher: Fetcher, max_lookups: int = MAX_LOOKUPS
) -> tuple[dict[str, Artist], dict]:
    """Fill each concert's `artists` keys; return the artist table and counts."""
    artists: dict[str, Artist] = {}
    capped = False

    def get(name: str) -> Artist | None:
        nonlocal capped
        key = norm(name)
        if key not in artists:
            if len(artists) >= max_lookups:
                capped = True
                return None
            artists[key] = _lookup(fetcher, name)
        return artists[key]

    for c in concerts:
        if len(c.lineup) >= 2:  # every act of the evening (WIP-72)
            c.artists = _lineup_artists(c, get)
            continue
        names, whole = performers_of(c)
        keys: list[str] = []
        if whole and (a := get(whole)) and a.identified:
            keys = [a.key]
        else:
            for name in names:
                a = get(name)
                if a and a.identified and a.key not in keys:
                    keys.append(a.key)
        c.artists = keys
    if capped:
        log.warning("Artists: lookup cap of %d reached", max_lookups)
    stats = {
        "candidates": len(artists),
        "identified": sum(1 for a in artists.values() if a.identified),
        "with_tags": sum(1 for a in artists.values() if a.tags),
        "confident": sum(1 for a in artists.values() if a.confident),
        **{
            f"doubt_{d}": sum(1 for a in artists.values() if a.doubt == d)
            for d in ("ambiguous", "unverified", "low_fans", "short_name")
        },
        "concerts_with_artist": sum(1 for c in concerts if c.artists),
        "capped": capped,
    }
    log.info("Artists: %s", stats)
    return {k: a for k, a in artists.items() if a.identified}, stats
