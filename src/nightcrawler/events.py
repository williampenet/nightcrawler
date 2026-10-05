"""Turn raw events into one clean concert list (PRD FR-2)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Concert, RawEvent, Venue

MUSIC_TYPES = {"MusicEvent", "Festival"}

MUSIC_WORDS = re.compile(
    r"\b(concerts?|live|dj|djs|showcase|r[ée]cital|jazz|rock|pop|rap|hip[- ]?hop|[ée]lectro"
    r"|techno|house|folk|punk|metal|noise|drone|impro\w*|musiques?|musical|orchestre"
    r"|quatuor|quartet|trio|chorale|op[ée]ra|soul|funk|blues|reggae|dub|chanson|soir[ée]e"
    r"|release party|tourn[ée]e|tour)\b",
    re.IGNORECASE,
)
NOT_MUSIC_WORDS = re.compile(
    r"\b(atelier|stage|exposition|expo|vernissage|conf[ée]rence|table ronde|projection"
    r"|cin[ée]ma|lecture|march[ée]|brocante|yoga|cours|visite|formation|r[ée]union)\b",
    re.IGNORECASE,
)


def concert_reason(event: RawEvent, venue: Venue | None) -> str | None:
    """Why this event counts as a concert, or None to drop it."""
    if MUSIC_TYPES & set(event.types):
        return "schema.org type"
    if event.source == "ticketmaster":
        return "ticketing category: music"
    text = " ".join(filter(None, (event.title, event.description)))
    if NOT_MUSIC_WORDS.search(event.title):
        return None
    if venue is not None and venue.is_music_venue:
        return "music venue"
    if MUSIC_WORDS.search(text):
        return "music keywords"
    return None


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def in_window(event: RawEvent, now: datetime, days: int) -> bool:
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_today <= event.start <= now + timedelta(days=days)


def build_concerts(
    raw: list[RawEvent],
    venues: dict[str, Venue],
    *,
    now: datetime,
    window_days: int,
    tz: ZoneInfo,
) -> list[Concert]:
    merged: dict[str, Concert] = {}
    for ev in raw:
        if not in_window(ev, now, window_days):
            continue
        venue = venues.get(ev.venue_id)
        reason = concert_reason(ev, venue)
        if reason is None:
            continue
        start = ev.start.astimezone(tz)
        key = f"{ev.venue_id}|{start.date().isoformat()}|{_slug(ev.title)}"
        cid = hashlib.sha1(key.encode()).hexdigest()[:12]
        if cid in merged:
            c = merged[cid]
            if ev.source not in c.sources:
                c.sources.append(ev.source)
            c.url = c.url or ev.url
            c.ticket_url = c.ticket_url or ev.ticket_url
            c.performers = c.performers or ev.performers
            continue
        merged[cid] = Concert(
            id=cid,
            title=ev.title,
            start=start.isoformat(),
            venue_id=ev.venue_id,
            venue_name=venue.name if venue else (ev.location_name or "?"),
            url=ev.url,
            ticket_url=ev.ticket_url,
            performers=ev.performers,
            sources=[ev.source],
            reason=reason,
        )
    return sorted(merged.values(), key=lambda c: (c.start, c.venue_name, c.title))
