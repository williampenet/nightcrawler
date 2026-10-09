"""Each concert's own page on the venue's site, for the page_llm reader (WIP-92).

page_llm events come from the agenda page, and the model's answer holds no link
(extract.SCHEMA), so each kept event's own page is found without a model, in the agenda HTML:

- links resolved against the agenda page's URL (fragment dropped), http(s), on the agenda's
  host (exact hostname after dropping a leading "www.", never netloc text), and not one of
  the agenda pages read;
- a link matches when its text, `title` or `aria-label`, normalised with `artists.norm`,
  equals the normalised title, or contains it when that title has at least MIN_CONTAINS
  characters;
- only a unique match is used (several links to the same URL are one match); otherwise the
  event keeps the agenda URL and no page is read.

The page's description: the `description` of its JSON-LD Event (structured.jsonld_events)
starting on the event's day, else of the page's single Event; else the page's main text
extracted by trafilatura (Apache-2.0 since 1.8.0, https://pypi.org/project/trafilatura/; no
model call). Plain text, whitespace normalised, capped at structured.MAX_TEXT like the
descriptions the other readers store (`structured._text`). The text is untrusted data: it is
stored in the listing, and the judge reads it as data (judge.description_text).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urldefrag, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
import trafilatura
from bs4 import BeautifulSoup

from ..artists import norm
from ..http import Fetcher, RobotsBlocked
from ..models import RawEvent
from ..structured import MAX_TEXT, jsonld_events

log = logging.getLogger(__name__)

DEFAULT_MAX_DETAILS = 40  # event pages read per venue (reader key `max_details`)
MIN_CONTAINS = 6  # a shorter title must equal the link text ("Live" is in many links)

Links = list[tuple[tuple[str, ...], str]]  # (normalised texts, absolute URL) per link


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


def page_links(html: str, base_url: str, hosts: set[str], agenda: set[tuple]) -> Links:
    """Links of an agenda page that may be an event's own page."""
    out: Links = []
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        try:
            url = urldefrag(urljoin(base_url, str(a["href"]).strip())).url
        except ValueError:
            continue
        if host(url) not in hosts or page_key(url) in agenda:
            continue
        raw = (a.get_text(" "), a.get("title"), a.get("aria-label"))
        texts = tuple(t for t in (norm(str(r)) for r in raw if r) if t)
        if texts:
            out.append((texts, url))
    return out


def event_link(title: str, links: Links) -> str | None:
    """The one URL whose link names this title, or None (no match, or several)."""
    key = norm(title)
    if not key:
        return None
    found = {
        url
        for texts, url in links
        if any(t == key or (len(key) >= MIN_CONTAINS and key in t) for t in texts)
    }
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


def description(html: str, start: datetime, tz: ZoneInfo) -> str:
    """The event's description on its own page, or "" (see the module docstring)."""
    try:
        events = jsonld_events(html, "event_page", tz)
    except Exception:  # e.g. JSON nested past the recursion limit
        events = []
    day = start.astimezone(tz).date()
    same_day = [e for e in events if e.start.astimezone(tz).date() == day]
    pick = same_day if len(same_day) == 1 else events if len(events) == 1 else []
    if pick and (text := _plain(pick[0].description)):
        return text
    return main_text(html)


@dataclass
class Counts:
    """Per venue, for the public report: counts only, never page text."""

    pages: int = 0  # event pages requested
    described: int = 0  # events given a description
    errors: int = 0  # requests that failed (robots.txt, HTTP error, status != 200)


def _get(fetcher: Fetcher, url: str) -> str | None:
    try:
        resp = fetcher.get(url)
    except (RobotsBlocked, httpx.HTTPError):
        return None
    return resp.text if resp.status == 200 else None


def read_details(
    events: list[RawEvent],
    fetcher: Fetcher,
    tz: ZoneInfo,
    cap: int,
    take: Callable[[], bool],
) -> tuple[Counts, list[str]]:
    """Fill the description of `events` (each with its own page as `url`) from their pages.

    At most `cap` pages for these events, each page read once; `take()` is the run-wide
    budget. An event whose page fails keeps its listing as it was."""
    counts, notes = Counts(), []
    pages: dict[str, str | None] = {}
    for ev in events:
        if ev.description or not ev.url:
            continue
        if ev.url not in pages:
            if counts.pages >= cap:
                notes.append(f"detail_cap: {cap}")
                break
            if not take():
                notes.append("detail_run_cap")
                break
            pages[ev.url] = _get(fetcher, ev.url)
            counts.pages += 1
            counts.errors += pages[ev.url] is None
        if (html := pages[ev.url]) and (text := description(html, ev.start, tz)):
            ev.description = text
            counts.described += 1
    return counts, notes
