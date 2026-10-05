"""Turn raw events into one clean concert list (PRD FR-2)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import Concert, RawEvent, Venue

MUSIC_TYPES = {"MusicEvent", "Festival"}

# Strong signals: enough to keep an event even if it also looks like another art form.
STRONG_MUSIC_WORDS = re.compile(
    r"\b(concerts?|live|dj|djs|showcase|r[ée]cital|jazz|rock|pop|rap|hip[- ]?hop|[ée]lectro"
    r"|techno|house|folk|punk|metal|noise|drone|musiques?|orchestre|quatuor|quartet"
    r"|chorale|op[ée]ra|soul|funk|blues|reggae|dub|chanson|release party)\b",
    re.IGNORECASE,
)
MUSIC_WORDS = re.compile(
    STRONG_MUSIC_WORDS.pattern + r"|\b(musical|trio|soir[ée]e|tourn[ée]e|tour)\b",
    re.IGNORECASE,
)
# Checked in the title only (ambiguous in a description, e.g. "on stage").
NOT_MUSIC_TITLE_WORDS = re.compile(
    r"\b(stage|expo|vernissage|table ronde|projection|cin[ée]ma|lecture|march[ée]|brocante"
    r"|yoga|cours|visite|formation|r[ée]union|th[ée][âa]tre)\b",
    re.IGNORECASE,
)
# Genre words, checked in the title and the description.
NOT_MUSIC_WORDS = re.compile(
    r"\b(atelier|ateliers|exposition|conf[ée]rence|impro|improvisations?|improvis[ée]e?s?"
    r"|humour|humoriste|stand[- ]?up|seule? en sc[èe]ne|one[- ](wo)?man[- ]show)\b",
    re.IGNORECASE,
)
NOT_MUSIC_TYPES = {"TheaterEvent", "ComedyEvent", "ExhibitionEvent", "EducationEvent"}


def concert_reason(event: RawEvent, venue: Venue | None) -> str | None:
    """Why this event counts as a concert, or None to drop it."""
    if MUSIC_TYPES & set(event.types):
        return "schema.org type"
    if event.source == "ticketmaster":
        return "ticketing category: music"
    text = " ".join(filter(None, (event.title, event.description)))
    looks_other = (
        NOT_MUSIC_WORDS.search(text)
        or NOT_MUSIC_TITLE_WORDS.search(event.title)
        or NOT_MUSIC_TYPES & set(event.types)
    )
    if looks_other and not STRONG_MUSIC_WORDS.search(text):
        return None
    if venue is not None and venue.is_music_venue:
        return "music venue"
    if MUSIC_WORDS.search(text):
        return "music keywords"
    return None


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


# Words too generic to identify a place ("Salle du Lavoir, Lyon" -> "lavoir").
GENERIC_PLACE_WORDS = re.compile(
    r"\b(le|la|les|l|du|de|des|d|au|aux|the|salle|club|bar|theatre|lyon|villeurbanne"
    r"|france)\b"
)
ADDRESS_RE = re.compile(r"^\s*\d|\b(rue|avenue|av|boulevard|bd|quai|cours|chemin)\b", re.I)
MIN_PLACE_KEY = 4


def place_key(name: str) -> str:
    """Normalised place name without accents, punctuation or generic words."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = GENERIC_PLACE_WORDS.sub(" ", re.sub(r"[^a-z0-9]+", " ", text))
    return re.sub(r"[^a-z0-9]+", "", text)


def _names_match(a: str, b: str) -> bool:
    short, long = sorted((a, b), key=len)
    return len(short) >= MIN_PLACE_KEY and short in long


def attribute_venue(
    event: RawEvent, venues: dict[str, Venue], keys: dict[str, str]
) -> tuple[str, str | None]:
    """(venue id, venue name if not a known venue) where the event actually takes place.

    Some venue sites list events held elsewhere (aggregators): the event's location
    wins over the page's venue when it clearly names another place.
    """
    loc = event.location_name
    page_venue = venues.get(event.venue_id)
    if event.source == "ticketmaster" or not loc or ADDRESS_RE.search(loc):
        return event.venue_id, None
    key = place_key(loc)
    if len(key) < MIN_PLACE_KEY:
        return event.venue_id, None
    if page_venue is not None and _names_match(key, keys.get(page_venue.id, "")):
        return event.venue_id, None
    matches = [vid for vid, vkey in keys.items() if _names_match(key, vkey)]
    if matches:
        return max(matches, key=lambda vid: len(keys[vid])), None
    return f"place:{key}", loc


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
    keys = {vid: place_key(v.name) for vid, v in venues.items()}
    for ev in raw:
        if not in_window(ev, now, window_days):
            continue
        venue_id, place_name = attribute_venue(ev, venues, keys)
        venue = venues.get(venue_id)
        reason = concert_reason(ev, venue)
        if reason is None:
            continue
        start = ev.start.astimezone(tz)
        key = f"{venue_id}|{start.date().isoformat()}|{_slug(ev.title)}"
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
            venue_id=venue_id,
            venue_name=venue.name if venue else (place_name or ev.location_name or "?"),
            url=ev.url,
            ticket_url=ev.ticket_url,
            performers=ev.performers,
            sources=[ev.source],
            reason=reason,
        )
    return sorted(merged.values(), key=lambda c: (c.start, c.venue_name, c.title))
