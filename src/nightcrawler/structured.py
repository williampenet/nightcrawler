"""Read events from machine-readable formats: schema.org JSON-LD, microdata, iCal.

Everything read here comes from third-party websites: it is treated as untrusted data.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from icalendar import Calendar

from .models import RawEvent

log = logging.getLogger(__name__)

MAX_TEXT = 500


def parse_start(value: Any, tz: ZoneInfo) -> datetime | None:
    """Parse a start date; naive values are read in the zone's timezone."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time(0, 0))
    else:
        try:
            dt = dateparser.isoparse(str(value).strip())
        except (ValueError, OverflowError):
            dt = _parse_full_date(str(value).strip())
            if dt is None:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _parse_full_date(text: str) -> datetime | None:
    """Free-form date, but only if it states day, month and year.

    dateutil fills missing parts from a default; parsing with two different defaults
    reveals which parts were really in the text.
    """
    try:
        a = dateparser.parse(text, dayfirst=True, default=datetime(1900, 1, 1))
        b = dateparser.parse(text, dayfirst=True, default=datetime(1904, 2, 2))
    except (ValueError, OverflowError):
        return None
    if (a.year, a.month, a.day) != (b.year, b.month, b.day):
        return None
    return a


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value")
    if value is None:
        return None
    text = " ".join(BeautifulSoup(str(value), "lxml").get_text(" ").split())
    return text[:MAX_TEXT] or None


def _url(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("@id")
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value.strip()
    return None


def _types(node: dict) -> list[str]:
    t = node.get("@type", [])
    types = t if isinstance(t, list) else [t]
    return [str(x).rsplit("/", 1)[-1] for x in types if x]


def _is_event(node: dict) -> bool:
    return any(t.endswith("Event") or t == "Festival" for t in _types(node))


def _walk(node: Any):
    """Yield every dict in a JSON-LD tree (handles @graph, ItemList, nesting)."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _performers(node: dict) -> list[str]:
    raw = node.get("performer") or node.get("performers") or []
    items = raw if isinstance(raw, list) else [raw]
    names = [_text(p) for p in items]
    return [n for n in names if n][:20]


def _offer_url(node: dict) -> str | None:
    offers = node.get("offers")
    items = offers if isinstance(offers, list) else [offers]
    for offer in items:
        if isinstance(offer, dict) and (u := _url(offer.get("url"))):
            return u
    return None


def event_from_schema(node: dict, venue_id: str, source: str, tz: ZoneInfo) -> RawEvent | None:
    title = _text(node.get("name"))
    start = parse_start(node.get("startDate"), tz)
    if not title or not start:
        return None
    location = node.get("location")
    return RawEvent(
        title=title,
        start=start,
        source=source,
        venue_id=venue_id,
        url=_url(node.get("url")),
        ticket_url=_offer_url(node),
        performers=_performers(node),
        description=_text(node.get("description")),
        types=_types(node),
        location_name=_text(location) if location else None,
    )


def jsonld_events(html: str, venue_id: str, tz: ZoneInfo) -> list[RawEvent]:
    soup = BeautifulSoup(html, "lxml")
    events: list[RawEvent] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue
        for node in _walk(data):
            if _is_event(node) and (ev := event_from_schema(node, venue_id, "json-ld", tz)):
                events.append(ev)
    return events


TYPE_RE = re.compile(r"^[A-Za-z]{1,40}$")
MAX_FORMATS = 12


def page_formats(html: str) -> list[str]:
    """The structured formats a page carries, for a report (WIP-89): "jsonld" and
    "jsonld:<schema.org type>" for each type found, "jsonld_invalid", "microdata",
    "next_data" (Next.js pages router) and "next_flight" (app router). Type names only, never
    page content."""
    soup = BeautifulSoup(html, "lxml")
    found: set[str] = set()
    for script in soup.find_all("script", type="application/ld+json"):
        found.add("jsonld")
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            found.add("jsonld_invalid")
            continue
        for node in _walk(data):
            found.update(f"jsonld:{t}" for t in _types(node) if TYPE_RE.match(t))
    if soup.find(attrs={"itemscope": True, "itemtype": True}):
        found.add("microdata")
    if soup.find("script", id="__NEXT_DATA__"):
        found.add("next_data")
    if "self.__next_f" in html:
        found.add("next_flight")
    flags = sorted(f for f in found if ":" not in f)
    return flags + sorted(f for f in found if ":" in f)[: MAX_FORMATS - len(flags)]


def microdata_events(html: str, venue_id: str, tz: ZoneInfo) -> list[RawEvent]:
    soup = BeautifulSoup(html, "lxml")
    events: list[RawEvent] = []
    for scope in soup.find_all(attrs={"itemscope": True, "itemtype": True}):
        itemtype = scope.get("itemtype", "")
        if "schema.org" not in itemtype or not itemtype.rstrip("/").endswith("Event"):
            continue

        def prop(name: str, scope=scope):
            for el in scope.find_all(attrs={"itemprop": name}):
                # skip properties of nested items (e.g. the location's name)
                owner = el.find_parent(attrs={"itemscope": True})
                if owner is scope:
                    return (
                        el.get("content") or el.get("datetime") or el.get("href") or el.get_text()
                    )
            return None

        node = {
            "@type": itemtype.rsplit("/", 1)[-1],
            "name": prop("name"),
            "startDate": prop("startDate"),
            "url": prop("url"),
            "description": prop("description"),
        }
        if ev := event_from_schema(node, venue_id, "microdata", tz):
            events.append(ev)
    return events


def ical_events(text: str, venue_id: str, tz: ZoneInfo) -> list[RawEvent]:
    try:
        cal = Calendar.from_ical(text)
    except (ValueError, IndexError, KeyError):
        return []
    events: list[RawEvent] = []
    for comp in cal.walk("VEVENT"):
        title = _text(str(comp.get("SUMMARY", "")))
        dtstart = comp.get("DTSTART")
        start = parse_start(dtstart.dt if dtstart else None, tz)
        if not title or not start:
            continue
        events.append(
            RawEvent(
                title=title,
                start=start,
                source="ical",
                venue_id=venue_id,
                url=_url(str(comp.get("URL", ""))),
                description=_text(str(comp.get("DESCRIPTION", ""))),
                location_name=_text(str(comp.get("LOCATION", ""))),
            )
        )
    return events
