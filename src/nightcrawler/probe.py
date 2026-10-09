"""Probe a venue website: find its agenda page and how to read it (PRD FR-1, layer 3)."""

from __future__ import annotations

import logging
import re
import threading
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .http import Fetcher, RobotsBlocked
from .models import Probe, RawEvent, Venue
from .structured import ical_events, jsonld_events, microdata_events, page_formats

log = logging.getLogger(__name__)

MAX_CANDIDATES = 4

AGENDA_RE = re.compile(
    r"agenda|programm|\bprog\b|concert|[ée]v[ée]nement|events?\b|calendrier|calendar"
    r"|billetterie|saison|spectacle|dates|line-?up",
    re.IGNORECASE,
)
# A "concerts" page is the most specific agenda; generic ones (billetterie, agenda) are
# often JS apps with nothing server-rendered.
CONCERT_RE = re.compile(r"\bconcerts?\b", re.IGNORECASE)
# Past events: still a fallback, but after any upcoming agenda.
PAST_RE = re.compile(r"\b(pass[ée]s?|archives?)\b", re.IGNORECASE)
SKIP_RE = re.compile(r"\.(pdf|jpe?g|png|gif|zip|mp3|mp4)$|mailto:|tel:|javascript:", re.I)

# Ticketing / listing platforms: recognised on venue sites; their pages are read with the
# same structured parsers when the venue site itself has no readable agenda (WIP-37).
PLATFORMS = {
    "shotgun": r"shotgun\.live",
    "dice": r"dice\.fm",
    "helloasso": r"helloasso\.com",
    "weezevent": r"weezevent\.com",
    "billetweb": r"billetweb\.fr",
    "yurplan": r"yurplan\.com",
    "seetickets": r"seetickets\.com",
    "fnac-spectacles": r"fnacspectacles\.com",
    "ticketmaster": r"ticketmaster\.(fr|com)",
    "bandsintown": r"bandsintown\.com",
    "songkick": r"songkick\.com",
    "facebook-events": r"facebook\.com/events",
}


def _same_site(url: str, base: str) -> bool:
    a, b = urlsplit(url).netloc.lower(), urlsplit(base).netloc.lower()
    strip = lambda h: h[4:] if h.startswith("www.") else h  # noqa: E731
    return strip(a) == strip(b) or strip(a).endswith("." + strip(b))


def _clean(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def agenda_candidates(html: str, base_url: str) -> list[str]:
    """Links on the page that look like an agenda, best first."""
    soup = BeautifulSoup(html, "lxml")
    scored: dict[str, float] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or SKIP_RE.search(href):
            continue
        url = _clean(urljoin(base_url, href))
        if not url.startswith(("http://", "https://")) or not _same_site(url, base_url):
            continue
        text = a.get_text(" ", strip=True)
        path = urlsplit(url).path
        score = (2 if AGENDA_RE.search(text) else 0) + (1 if AGENDA_RE.search(href) else 0)
        if score and (CONCERT_RE.search(text) or CONCERT_RE.search(path)):
            score += 3
        if score and (PAST_RE.search(text) or PAST_RE.search(path)):
            score = 0.5
        if score:
            scored[url] = max(scored.get(url, 0), score)
    ordered = sorted(scored, key=lambda u: (-scored[u], len(u)))
    return [u for u in ordered if u != _clean(base_url)][:MAX_CANDIDATES]


def ical_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for link in soup.find_all("link", type="text/calendar"):
        if link.get("href"):
            links.append(urljoin(base_url, link["href"]))
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("webcal://"):
            links.append("https://" + href[len("webcal://") :])
        elif href.lower().endswith(".ics") or "ical=1" in href.lower():
            links.append(urljoin(base_url, href))
    # WordPress "The Events Calendar" exposes an iCal export at ?ical=1
    if "tribe-events" in html and not links:
        sep = "&" if "?" in base_url else "?"
        links.append(base_url + sep + "ical=1")
    seen: list[str] = []
    for link in links:
        if link.startswith(("http://", "https://")) and link not in seen:
            seen.append(link)
    return seen[:3]


def platforms_in(html: str) -> list[str]:
    return sorted(name for name, pattern in PLATFORMS.items() if re.search(pattern, html, re.I))


MAX_PLATFORM_PAGES_PER_VENUE = 2
MAX_PLATFORM_PAGES_PER_RUN = 40
# Platforms whose pages we follow (the others in PLATFORMS are recognition-only), matched
# on the full hostname: never on netloc text, which may carry a spoofing "user@" part.
FOLLOWED_PLATFORMS = {
    name: re.compile(r"^(?:[\w-]+\.)*" + domain + "$")
    for name, domain in {
        "shotgun": r"shotgun\.live",
        "dice": r"dice\.fm",
        "helloasso": r"helloasso\.com",
        "weezevent": r"weezevent\.com",
        "billetweb": r"billetweb\.fr",
        "yurplan": r"yurplan\.com",
    }.items()
}
# Path segments of a platform's generic pages (home, locale, help...): not worth a fetch
GENERIC_PLATFORM_SEGMENTS = {
    "fr", "en", "fr-fr", "en-gb", "en-us", "app", "apps", "download", "help", "faq",
    "about", "contact", "search", "legal", "privacy", "terms", "cgu", "cgv", "blog", "pro",
    "organisateurs",
}  # fmt: skip
# Account, checkout and widget pages: never followed, wherever the segment appears
DENIED_PLATFORM_SEGMENTS = {
    "login", "signin", "signup", "register", "account", "auth", "oauth", "checkout",
    "cart", "basket", "ticket", "widget", "adhesions",
}  # fmt: skip
SKIP_ASSET_RE = re.compile(r"\.(js|css|svg|ico|woff2?)$", re.I)


def platform_of(url: str) -> str | None:
    """Name of the followed platform a URL belongs to, or None (also for unsafe URLs)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or "@" in parts.netloc or host.startswith("widget."):
        return None
    return next((n for n, rx in FOLLOWED_PLATFORMS.items() if rx.match(host)), None)


def platform_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """(platform, url) of links and embeds pointing to a specific platform page (venue,
    organiser or event), not to the platform's homepage, generic or account pages."""
    soup = BeautifulSoup(html, "lxml")
    found: list[tuple[str, str]] = []
    for el in soup.find_all(["a", "iframe"]):  # document order
        raw = ((el.get("href") if el.name == "a" else el.get("src")) or "").strip()
        url = _clean(urljoin(base_url, raw))  # "//host/..." takes the page's scheme
        path = urlsplit(url).path
        segments = {s for s in path.lower().split("/") if s}
        if (
            SKIP_RE.search(url)
            or SKIP_ASSET_RE.search(path)
            or segments <= GENERIC_PLATFORM_SEGMENTS
            or segments & DENIED_PLATFORM_SEGMENTS
        ):
            continue
        name = platform_of(url)
        if name and (name, url) not in found:
            found.append((name, url))
    return found


class PlatformBudget:
    """Run-wide cap on platform page fetches, shared by the probe threads."""

    def __init__(self, limit: int = MAX_PLATFORM_PAGES_PER_RUN) -> None:
        self.left = limit
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.left <= 0:
                return False
            self.left -= 1
            return True


def read_platform_page(
    name: str, url: str, venue_id: str, fetcher: Fetcher, tz: ZoneInfo, budget: PlatformBudget
) -> tuple[dict, list[RawEvent]]:
    """Read one platform page with the structured parsers.

    Its events belong to the venue that linked it; events.attribute_venue moves them to
    another known venue named by their location, or drops them (out of the zone).
    """
    page = {"platform": name, "url": url, "status": "no_events", "events": 0}
    if not fetcher.allowed(url):  # checked first: a blocked page costs no budget
        return page | {"status": "robots_blocked"}, []
    if not budget.take():
        return page | {"status": "skipped_budget"}, []
    try:
        resp = fetcher.get(url)
    except RobotsBlocked:  # e.g. redirected to a disallowed host
        return page | {"status": "robots_blocked"}, []
    except httpx.HTTPError as exc:
        return page | {"status": "fetch_error", "detail": type(exc).__name__}, []
    if resp.status != 200 or "html" not in resp.content_type:
        return page | {"status": "fetch_error", "detail": f"HTTP {resp.status}"}, []
    _, events = read_page(resp.text, venue_id, tz)
    if not events:
        for link in ical_links(resp.text, resp.url)[:1]:
            if not budget.take():
                break
            try:
                cal = fetcher.get(link)
            except (RobotsBlocked, httpx.HTTPError):
                continue
            if cal.status == 200 and "BEGIN:VCALENDAR" in cal.text[:2000]:
                events = ical_events(cal.text, venue_id, tz)
    for ev in events:
        ev.source = f"platform:{name}"
        ev.url = ev.url or resp.url  # the event's own URL when it has one
        ev.ticket_url = ev.ticket_url or resp.url
    if events:
        page |= {"status": "events", "events": len(events)}
    else:  # why nothing was read: which formats the page carries (WIP-89)
        page["formats"] = page_formats(resp.text)
    return page, events


def read_page(html: str, venue_id: str, tz: ZoneInfo) -> tuple[str | None, list[RawEvent]]:
    events = jsonld_events(html, venue_id, tz)
    if events:
        return "json-ld", events
    events = microdata_events(html, venue_id, tz)
    if events:
        return "microdata", events
    return None, []


def probe_venue(
    venue: Venue, fetcher: Fetcher, tz: ZoneInfo, budget: PlatformBudget | None = None
) -> tuple[Probe, list[RawEvent]]:
    if not venue.website:
        return Probe(venue.id, "no_website"), []
    try:
        home = fetcher.get(venue.website)
    except RobotsBlocked:
        return Probe(venue.id, "robots_blocked", agenda_url=venue.website), []
    except httpx.HTTPError as exc:
        return Probe(venue.id, "fetch_error", detail=type(exc).__name__), []
    if home.status != 200:
        return Probe(venue.id, "fetch_error", detail=f"HTTP {home.status}"), []

    pages = [(home.url, home.text)]
    platforms = set(platforms_in(home.text))
    for url in agenda_candidates(home.text, home.url):
        try:
            resp = fetcher.get(url)
        except (RobotsBlocked, httpx.HTTPError):
            continue
        if resp.status == 200 and "html" in resp.content_type:
            pages.append((resp.url, resp.text))
            platforms |= set(platforms_in(resp.text))

    # 1. structured events embedded in the pages (all pages: duplicates are merged later)
    found: list[RawEvent] = []
    best_method, best_url, best_count = None, None, 0
    for url, html in pages:
        method, events = read_page(html, venue.id, tz)
        found.extend(events)
        if len(events) > best_count:
            best_method, best_url, best_count = method, url, len(events)
    if found:
        probe = Probe(venue.id, "structured", best_method, best_url, len(found))
        probe.platforms = sorted(platforms)
        return probe, found

    # 2. iCal feeds linked from the pages
    for url, html in pages:
        for link in ical_links(html, url):
            try:
                resp = fetcher.get(link)
            except (RobotsBlocked, httpx.HTTPError):
                continue
            if resp.status == 200 and "BEGIN:VCALENDAR" in resp.text[:2000]:
                events = ical_events(resp.text, venue.id, tz)
                if events:
                    probe = Probe(venue.id, "structured", "ical", link, len(events))
                    probe.platforms = sorted(platforms)
                    return probe, events

    agenda_url = pages[1][0] if len(pages) > 1 else home.url
    if not platforms:
        return Probe(venue.id, "no_agenda", agenda_url=agenda_url), []

    # 3. pages on the ticketing platforms the venue links to (WIP-37)
    probe = Probe(venue.id, "platform_only", agenda_url=agenda_url, platforms=sorted(platforms))
    budget = budget or PlatformBudget()
    links: list[tuple[str, str]] = []
    for url, html in pages:
        links += [link for link in platform_links(html, url) if link not in links]
    best_count, slots = 0, MAX_PLATFORM_PAGES_PER_VENUE
    for name, url in links:
        if slots == 0:
            break
        page, events = read_platform_page(name, url, venue.id, fetcher, tz, budget)
        probe.platform_pages.append(page)
        slots -= page["status"] != "robots_blocked"  # a blocked link costs no slot
        found.extend(events)
        if len(events) > best_count:
            probe.method, probe.agenda_url, best_count = f"platform:{name}", url, len(events)
    if found:
        probe.status, probe.events_found = "structured", len(found)
    return probe, found
