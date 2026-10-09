"""Each concert's own page on the venue's site, for the page_llm reader (WIP-92).

page_llm events come from the agenda page, and the model's answer holds no link
(extract.SCHEMA), so each kept event's own page is found without a model, in the agenda HTML:

- links resolved against the agenda page's URL (fragment dropped), http(s), on the agenda's
  host (exact hostname after dropping a leading "www.", never netloc text), not one of the
  agenda pages read, and not a booking or series link (BOOKING_OR_SERIES in its path, query or
  text: "Réserver", /billetterie/, /cycle/jazz-club/);
- a link matches when its text, `title` or `aria-label`, normalised with `artists.norm`,
  equals the normalised title; only when no link does, when the title's words appear in a row
  among the link's words and the title has at least MIN_CONTAINS characters ("Voir FAKEAR";
  "Mantra" never matches "Mantrasonic");
- only a unique match is used (several links to the same URL are one match); otherwise the
  event keeps the agenda URL and no page is read.

The page is used only if it answers 200 from the agenda's host (a redirect elsewhere is an
error). Its description: the `description` of its JSON-LD Event starting on the event's day
(structured.jsonld_events), else its main text extracted by trafilatura (Apache-2.0 since
1.8.0, https://pypi.org/project/trafilatura/; no model call). If the page has JSON-LD Events
and none starts that day, it is probably another concert's: no description, and the event
gets the agenda URL back. Plain text, whitespace normalised, capped at structured.MAX_TEXT
like the descriptions the other readers store (`structured._text`). The text is untrusted
data: it is stored in the listing, and the judge reads it as data (judge.description_text).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urldefrag, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
import trafilatura
from bs4 import BeautifulSoup

from ..artists import norm
from ..http import Fetcher, Response, RobotsBlocked
from ..models import RawEvent
from ..structured import MAX_TEXT, jsonld_events

log = logging.getLogger(__name__)

DEFAULT_MAX_DETAILS = 40  # event pages fetched per venue (reader key `max_details`)
MIN_CONTAINS = 6  # a shorter title must equal the link text ("Live" is in many links)
# normalised word prefixes of booking and series links (review of WIP-92)
BOOKING_OR_SERIES = ("billet", "reserver", "reservation", "reservez", "ticket", "cycle", "saison")

Words = tuple[str, ...]
Links = list[tuple[tuple[Words, ...], str]]  # (normalised words of each text, URL) per link


def words(text: str) -> Words:
    """Normalised words: their concatenation is `artists.norm(text)`."""
    return tuple(w for w in (norm(x) for x in re.split(r"[\W_]+", text)) if w)


def host(url: str) -> str | None:
    """Hostname without a leading "www." for an http(s) URL, else None. `hostname` drops
    userinfo and port: "https://larayonne.org@evil.example/" is evil.example."""
    try:
        parts = urlsplit(url)
        name = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not name:
        return None
    return name.removeprefix("www.")


def page_key(url: str) -> tuple:
    """What makes two URLs the same page here: host, path without trailing slash, query."""
    parts = urlsplit(url)
    return host(url), parts.path.rstrip("/"), parts.query


def _booking(ws: Words) -> bool:
    return any(w.startswith(BOOKING_OR_SERIES) for w in ws)


def page_links(html: str, base_url: str, hosts: set[str], agenda: set[tuple]) -> Links:
    """Links of an agenda page that may be an event's own page."""
    out: Links = []
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        try:
            url = urldefrag(urljoin(base_url, str(a["href"]).strip())).url
            parts = urlsplit(url)
        except ValueError:
            continue
        if host(url) not in hosts or page_key(url) in agenda:
            continue
        raw = (a.get_text(" "), a.get("title"), a.get("aria-label"))
        texts = tuple(ws for ws in (words(str(r)) for r in raw if r) if ws)
        if texts and not any(map(_booking, (*texts, words(f"{parts.path} {parts.query}")))):
            out.append((texts, url))
    return out


def _contains(ws: Words, key: Words) -> bool:
    n = len(key)
    return any(ws[i : i + n] == key for i in range(len(ws) - n + 1))


def event_link(title: str, links: Links) -> str | None:
    """The one URL whose link names this title, or None (no match, or several)."""
    key = words(title)
    joined = "".join(key)
    if not joined:
        return None
    found = {url for texts, url in links if any("".join(ws) == joined for ws in texts)}
    if not found and len(joined) >= MIN_CONTAINS:
        found = {url for texts, url in links if any(_contains(ws, key) for ws in texts)}
    return found.pop() if len(found) == 1 else None


def _plain(text: str | None) -> str:
    return " ".join((text or "").split())[:MAX_TEXT]


def main_text(html: str) -> str:
    try:
        return _plain(
            trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                include_images=False,
                include_links=False,
            )
        )
    except Exception:  # a page trafilatura cannot parse: no text, the event stays
        return ""


def description(html: str, start: datetime, tz: ZoneInfo) -> str | None:
    """The event's description on its own page, "" if none, or None when the page's JSON-LD
    Events all start on other days (probably another concert's page)."""
    try:
        events = jsonld_events(html, "event_page", tz)
    except Exception:  # e.g. JSON nested past the recursion limit
        events = []
    day = start.astimezone(tz).date()
    same_day = [e for e in events if e.start.astimezone(tz).date() == day]
    if events and not same_day:
        return None
    if len(same_day) == 1 and (text := _plain(same_day[0].description)):
        return text
    return main_text(html)


@dataclass
class Counts:
    """Per venue, for the public report: counts only, never page text."""

    pages: int = 0  # event pages read (fetched or from the fetcher's cache)
    cached: int = 0  # of which from the cache (no request, outside the caps)
    described: int = 0  # events given a description
    errors: int = 0  # robots.txt, HTTP error, status != 200, redirect off the agenda's host
    other_day: int = 0  # JSON-LD Events of other days only: the agenda URL is kept


def _text(resp: Response | None, hosts: set[str]) -> str | None:
    if resp is None or resp.status != 200 or host(resp.url) not in hosts:
        return None
    return resp.text


def _get(fetcher: Fetcher, url: str) -> Response | None:
    try:
        return fetcher.get(url)
    except (RobotsBlocked, httpx.HTTPError):
        return None


def read_details(
    events: list[tuple[RawEvent, str]],
    fetcher: Fetcher,
    tz: ZoneInfo,
    cap: int,
    take: Callable[[], bool],
    hosts: set[str],
) -> tuple[Counts, list[str]]:
    """Fill the description of each (event with its own page as `url`, agenda URL).

    Each page is read once. At most `cap` requests, and `take()` (the run-wide budget) before
    each; once a cap is reached, pages already read or cached are still used. An event whose
    page fails keeps its listing as it was."""
    counts, notes = Counts(), []
    pages: dict[str, str | None] = {}
    fetched = 0
    for ev, agenda_url in events:
        url = ev.url
        if ev.description or not url:
            continue
        if url not in pages:
            resp = fetcher.cached(url)
            if resp is not None:
                counts.cached += 1
            elif fetched >= cap:
                notes += [] if f"detail_cap: {cap}" in notes else [f"detail_cap: {cap}"]
                continue
            elif not take():
                notes += [] if "detail_run_cap" in notes else ["detail_run_cap"]
                continue
            else:
                fetched += 1
                resp = _get(fetcher, url)
            pages[url] = _text(resp, hosts)
            counts.pages += 1
            counts.errors += pages[url] is None
        if (html := pages[url]) is None:
            continue
        text = description(html, ev.start, tz)
        if text is None:
            ev.url = agenda_url
            counts.other_day += 1
        elif text:
            ev.description = text
            counts.described += 1
    return counts, notes
