"""Events from a venue's listing page(s), then the schema.org Event JSON-LD of each detail page.

Many venue sites publish no Event data on their agenda page but one JSON-LD block per event
page (capture of 2026-10-07: L'Épicerie Moderne, Marché Gare, Auditorium de Lyon, Opéra
Underground). Which listing URLs, which links count as events, and how many detail pages to
read are set per venue under `priority_venues` in `config/zone.yaml` (WIP-62).

- A URL may hold `{yyyymm}`: it is read once per month of the run window.
- Links are kept when they stay on the listing's host and their path matches `include` and
  not `exclude` (regexes); at most `max_details` detail pages are read, in listing order.
- The JSON-LD reader is `structured.jsonld_events` (top-level object, `@graph`, arrays,
  `@type` as a full schema.org URL; a date without offset is read in the zone's timezone).
- Events are attached to the configured venue, never to the JSON-LD location (Opéra
  Underground pages name the building, "Opéra de Lyon"), and link to the page they came from.

Everything read is untrusted data: titles are plain text (structured._text), links http(s).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urldefrag, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from ..events import in_window
from ..http import Fetcher, RobotsBlocked
from ..models import RawEvent
from ..structured import jsonld_events

DEFAULT_MAX_DETAILS = 60
MONTH_TOKEN = "{yyyymm}"


def listing_urls(reader: dict, now: datetime, window_days: int) -> list[str]:
    """The configured URLs, `{yyyymm}` expanded for every month from now to the window's end."""
    end = (now + timedelta(days=window_days)).date()
    urls: list[str] = []
    for url in reader["urls"]:
        if MONTH_TOKEN not in url:
            urls.append(url)
            continue
        year, month = now.year, now.month
        while (year, month) <= (end.year, end.month):
            urls.append(url.replace(MONTH_TOKEN, f"{year:04d}{month:02d}"))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return list(dict.fromkeys(urls))


def event_links(html: str, base_url: str, include: str, exclude: str | None) -> list[str]:
    """Absolute links on the page's own scheme and host (no other site, no http downgrade)
    whose path matches `include` and not `exclude`."""
    origin = urlsplit(base_url)
    links: list[str] = []
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        url = urldefrag(urljoin(base_url, a["href"].strip())).url
        parts = urlsplit(url)
        if (parts.scheme, parts.netloc.lower()) != (origin.scheme, origin.netloc.lower()):
            continue
        if re.search(include, parts.path) and not (exclude and re.search(exclude, parts.path)):
            links.append(url)
    return list(dict.fromkeys(links))


def read(
    reader: dict,
    venue: str,
    fetcher: Fetcher,
    now: datetime,
    tz: ZoneInfo,
    window_days: int,
) -> tuple[list[RawEvent], int, str]:
    """(events in the window, pages read, status). A broken listing or detail page is counted
    in the status; the venue fails only when no listing page could be read."""
    urls = listing_urls(reader, now, window_days)
    host = urlsplit(urls[0]).netloc.lower()
    source = f"listing_jsonld:{host}"
    cap = int(reader.get("max_details", DEFAULT_MAX_DETAILS))
    links: list[str] = []
    pages, listing_errors, first_error = 0, 0, ""
    for url in urls:  # robots.txt refusing a listing page fails the venue (robots_blocked)
        try:
            resp = fetcher.get(url)
            error = "" if resp.status == 200 else f"HTTP {resp.status}"
        except httpx.HTTPError as exc:
            error = type(exc).__name__
        if error:  # e.g. a month not published yet: the other pages still count
            listing_errors += 1
            first_error = first_error or error
            continue
        pages += 1
        links += event_links(resp.text, resp.url, reader["include"], reader.get("exclude"))
    if not pages:
        raise ValueError(f"no listing page read ({first_error})")
    links = list(dict.fromkeys(links))
    notes = [f"detail_cap: {cap} of {len(links)} links"] if len(links) > cap else []
    events: list[RawEvent] = []
    detail_errors = 0
    for link in links[:cap]:
        try:
            resp = fetcher.get(link)
        except (RobotsBlocked, httpx.HTTPError):
            resp = None
        if resp is None or resp.status != 200:
            detail_errors += 1
            continue
        pages += 1
        for ev in jsonld_events(resp.text, source, tz):
            ev.source, ev.location_name, ev.url = source, venue, link
            if in_window(ev, now, window_days):
                events.append(ev)
    if listing_errors:
        notes.append(f"listing errors: {listing_errors} ({first_error})")
    if detail_errors:
        notes.append(f"detail errors: {detail_errors}")
    return events, pages, "; ".join(notes) or "ok"
