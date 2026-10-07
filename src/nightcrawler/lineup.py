"""Every act of an evening (WIP-72): the line-up of a concert.

The line-up is the union of the performers of every merged listing, in order, without
duplicates. A listing without performers gives the acts split from its title:

- hard separators " + ", " / ", " x " (lower case), " b2b " and "première partie" always
  split ("Lucio Bukowski + Anton Serra + OSter Lapwass (nouvelle date)");
- soft separators ", " and " & " split too, unless the part is one name a source gave as a
  performer ("Simon & Garfunkel"). The artist lookup (artists.py) later joins the parts back
  when the whole title or the "A & B" part is a known artist ("Earth, Wind & Fire");
- trailing descriptors are dropped: "(avant-garde/free rock, Japon)", "(Us)", "(complet)",
  a quoted show name ('Leïla Martial & Elie Dufour "Karma Bazar"'), " - Sold out", " - 18:00";
- parts that are a price ("12€"), a time ("20h30"), a status or a placeholder ("Complet",
  "Gratuit", "DJ set", "2 soirées") are not acts;
- prefixes "Concert :", "Live :", "Release party :", "Soirée <name> :" are dropped. Any other
  "X : Y" title is more likely "Artist : tour name" ("Pord : Tournée Rouge & Noir"): it splits
  only on the right side when a hard separator gives two acts ("Nuit noise : Pord + Ana"),
  or on the left side when it holds two acts, else stays one act (artists.py then looks up
  each side, as for any one-act title).

Gancio descriptions add names when the first paragraph is only "Name (genre, country)"
lines (description_acts). Everything here is deterministic: no model reads the titles.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from bs4 import BeautifulSoup

MAX_LINEUP = 20  # a festival page may list more; the card stays readable
MAX_DESCRIPTION_LINES = 8
MIN_ACT, MAX_ACT = 2, 60  # characters, as artists.py

# placeholders, never an act (keys as act_key() gives them)
PLACEHOLDERS = {
    *("variousartists artistesdivers divers invites invite invitees invitee guests guest".split()),
    *("specialguest specialguests tba tbc dj djs unknown inconnu more friends".split()),
    *("1erepartie 1repartie premierepartie firstpartie".split()),
    *("complet soldout gratuit prixlibre djset annule annulee entreelibre".split()),
}
# a price, a time or "2 soirées" given as a part: "Pord / 12€ / 20h"
NOT_ACT_RE = re.compile(
    r"^(?:\d+(?:[.,]\d+)?\s*€|\d{1,2}\s*[h:]\s*\d{0,2}|\d+\s*soir[ée]es?)$", re.IGNORECASE
)

PREFIX_RE = re.compile(
    r"^\s*(?:concert|live|showcase|release party|soir[ée]e(?:\s+[^:]{1,40}?)?)\s*:\s*",
    re.IGNORECASE,
)
COLON_RE = re.compile(r"\s*:\s+")
BRACKETS_RE = re.compile(r"\[[^\]]*\]")
# "première partie" names the support act that follows (or nobody, at the end)
SUPPORT_RE = re.compile(
    r"\s*(?:[-–—+:]\s*)?\b(?:1\s*[eè]?re|premi[eè]re|first)\s+partie\b\s*:?\s*", re.IGNORECASE
)
HARD_RE = re.compile(r"\s+[+/]\s+|\sx\s|\s+(?i:b2b)\s+")
SOFT_RE = re.compile(r"\s*,\s+|\s+&\s+")
PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
QUOTED_RE = re.compile(r"\s+[\"“«][^\"“”«»]{1,80}[\"”»]\s*$")
STATUS_RE = re.compile(
    r"\s+[-–—]\s+(?:sold[- ]?out|complet|annul[ée]e?|report[ée]e?"
    r"|\d{1,2}\s*[h:]\s*\d{0,2}|\d+(?:[.,]\d+)?\s*€)\s*$",
    re.IGNORECASE,
)
PAREN_SPAN_RE = re.compile(r"\([^()]*\)")
# Gancio first paragraph: "Tomoyuki Aoki & Harutaka Mochizuki (avant-garde/free rock, Japon)"
DESCRIPTION_LINE_RE = re.compile(r"^(?P<name>[^()]{2,80}?)\s*\((?P<info>[^()]*,[^()]*)\)\s*$")


def act_key(name: str) -> str:
    """Comparable key: lower case, no accents, alphanumerics only (= artists.norm)."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _split(text: str, sep: re.Pattern[str]) -> list[str]:
    """Split on `sep` outside parentheses: "(rock / noise, Japon)" stays one descriptor."""
    spans = [m.span() for m in PAREN_SPAN_RE.finditer(text)]
    parts, last = [], 0
    for m in sep.finditer(text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        parts.append(text[last : m.start()])
        last = m.end()
    parts.append(text[last:])
    return parts


def _strip(text: str) -> str:
    """One act or segment without its trailing descriptors."""
    text = " ".join(text.split()).strip(" -–—:")
    while True:
        new = STATUS_RE.sub("", QUOTED_RE.sub("", PAREN_RE.sub("", text))).strip(" -–—:")
        if new == text or not new:
            return text
        text = new


def _valid(name: str) -> bool:
    key = act_key(name)
    ok = MIN_ACT <= len(name) <= MAX_ACT and bool(key) and key not in PLACEHOLDERS
    return ok and not NOT_ACT_RE.match(name)


def _groups(text: str, keep: frozenset[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    for segment in _split(SUPPORT_RE.sub(" + ", text), HARD_RE):
        segment = _strip(segment)
        if not segment:
            continue
        parts = [segment] if act_key(segment) in keep else _split(segment, SOFT_RE)
        acts = [a for a in (_strip(p) for p in parts) if _valid(a)]
        if acts:
            groups.append(acts)
    return groups


def parse_title(title: str, keep: Iterable[str] = ()) -> tuple[str, list[list[str]]]:
    """(whole cleaned title, acts grouped by hard segment) of a listing's title.

    `keep`: act keys a source gave as performers; such a part is never split.
    """
    keep = frozenset(keep)
    text = PREFIX_RE.sub("", BRACKETS_RE.sub(" ", title))
    text = _strip(text)
    if COLON_RE.search(text):
        left, right = COLON_RE.split(text, maxsplit=1)
        groups = _groups(right, keep)
        if len(groups) >= 2:  # "Event name : A + B"; a soft split may be a tour name
            return _strip(right), groups
        groups = _groups(left, keep)
        if sum(map(len, groups)) >= 2:  # "A & B : tour name"
            return _strip(left), groups
        whole = text
        return whole, ([[whole]] if _valid(whole) else [])
    return text, _groups(text, keep)


def split_title(title: str, keep: Iterable[str] = ()) -> list[str]:
    """The acts of a title, in order, without duplicates."""
    return _unique(a for g in parse_title(title, keep)[1] for a in g)


def _unique(names: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        n = " ".join(str(n).split())
        key = act_key(n)
        if _valid(n) and key not in seen:
            seen.add(key)
            out.append(n)
    return out


def merge_lineup(listings: Iterable[tuple[str, list[str], list[str]]]) -> list[str]:
    """Line-up of merged listings, given best source first as (title, performers, billed).

    A listing with performers gives them. One without gives its title's acts, but when
    another listing names performers its split must share one of them: else the title is
    more likely "Act - Tour name" than a line-up. `billed` are names from the description.
    """
    listings = list(listings)
    keep = {act_key(p) for _, performers, _ in listings for p in performers} - PLACEHOLDERS
    names: list[str] = []
    for title, performers, billed in listings:
        if performers:
            names += performers
        else:
            acts = split_title(title, keep)
            if not keep or keep & {act_key(a) for a in acts}:
                names += acts
        for name in billed:
            names += split_title(name, keep)
    return _unique(names)[:MAX_LINEUP]


def description_acts(html: str | None) -> list[str]:
    """Names of a description whose first paragraph is only "Name (genre, country)" lines.

    Conservative: one line that does not match, or more than MAX_DESCRIPTION_LINES, and
    nothing is taken. The names are returned whole; merge_lineup splits "A & B".
    """
    if not html or not isinstance(html, str):
        return []
    soup = BeautifulSoup(html, "lxml")
    first = soup.find("p")
    if first is not None:
        for br in first.find_all("br"):
            br.replace_with("\n")
        text = first.get_text()
    else:
        text = re.split(r"\n\s*\n", soup.get_text(), maxsplit=1)[0]
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines or len(lines) > MAX_DESCRIPTION_LINES:
        return []
    matches = [DESCRIPTION_LINE_RE.match(line) for line in lines]
    if not all(matches):
        return []
    return [m["name"].strip() for m in matches if m]
