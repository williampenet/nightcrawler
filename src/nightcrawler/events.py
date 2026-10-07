"""Turn raw events into one clean concert list (PRD FR-2)."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .dedup import dedupe, make_links
from .models import Concert, RawEvent, Venue

MUSIC_TYPES = {"MusicEvent", "Festival"}

# A performance: the only thing that keeps an activity ("Atelier + concert").
PERFORMANCE_WORDS = re.compile(
    r"\b(concerts?|showcase|r[ée]cital|release party)\b",
    re.IGNORECASE,
)
# Strong music signals: they keep an event that also looks like another art form.
STRONG_MUSIC_WORDS = re.compile(
    PERFORMANCE_WORDS.pattern
    + r"|\b(dj|djs|jazz|rock|pop|rap|hip[- ]?hop|[ée]lectro|techno|house|folk|punk|metal"
    r"|noise|drone|musiques?|orchestre|quatuor|quartet|chorale|op[ée]ra|soul|funk|blues"
    r"|reggae|dub|chanson)\b",
    re.IGNORECASE,
)
# Weak signals: enough at a non-music venue, never enough to override an exclusion.
MUSIC_WORDS = re.compile(
    STRONG_MUSIC_WORDS.pattern + r"|\b(live|musical|trio|soir[ée]e|tourn[ée]e|tour)\b",
    re.IGNORECASE,
)
# Activities (not shows): only a performance word overrides them ("Atelier DJ" is dropped).
# Ambiguous words ("stage", "cours", ...) count in the title only.
ACTIVITY_WORDS = re.compile(r"\b(ateliers?|exposition|conf[ée]rences?)\b", re.IGNORECASE)
ACTIVITY_TITLE_WORDS = re.compile(
    r"\b(stage|expo|vernissage|table ronde|projection|cin[ée]ma|lecture|march[ée]|brocante"
    r"|yoga|cours|visite|formation|r[ée]union)\b",
    re.IGNORECASE,
)
ACTIVITY_TYPES = {"ExhibitionEvent", "EducationEvent"}
# Other performing arts: a strong music signal overrides them ("Impro jazz" is kept).
OTHER_SHOW_WORDS = re.compile(
    r"\b(impro|improvisations?|improvis[ée]e?s?|humour|humoriste|stand[- ]?up"
    r"|seule? en sc[èe]ne|one[- ](wo)?man[- ]show)\b",
    re.IGNORECASE,
)
OTHER_SHOW_TITLE_WORDS = re.compile(r"\bth[ée][âa]tre\b", re.IGNORECASE)  # often a venue name
OTHER_SHOW_TYPES = {"TheaterEvent", "ComedyEvent"}


def concert_reason(event: RawEvent, venue: Venue | None) -> str | None:
    """Why this event counts as a concert, or None to drop it."""
    if MUSIC_TYPES & set(event.types):
        return "schema.org type"
    if event.source == "ticketmaster":
        return "ticketing category: music"
    text = " ".join(filter(None, (event.title, event.description)))
    types = set(event.types)
    activity = (
        ACTIVITY_WORDS.search(text)
        or ACTIVITY_TITLE_WORDS.search(event.title)
        or ACTIVITY_TYPES & types
    )
    if activity and not PERFORMANCE_WORDS.search(text):
        return None
    other_show = (
        OTHER_SHOW_WORDS.search(text)
        or OTHER_SHOW_TITLE_WORDS.search(event.title)
        or OTHER_SHOW_TYPES & types
    )
    if other_show and not STRONG_MUSIC_WORDS.search(text):
        return None
    if venue is not None and venue.is_music_venue:
        return "music venue"
    if MUSIC_WORDS.search(text):
        return "music keywords"
    return None


MUSIC_TAGS = {"concert", "concerts", "musique live", "dj set", "live", "dj"}
NOT_MUSIC_TAGS = {
    "théâtre",
    "theatre",
    "conférence",
    "discussion",
    "humour",
    "stand-up",
    "exposition",
    "projection",
    "atelier",
    "bouffe",
}


def tag_reason(tags: list[str]) -> str | None:
    """Decide from source tags: "tag: <tag>" keeps, "" drops, None = no verdict."""
    tags = [t.strip().lower() for t in tags]
    music = next((t for t in tags if t in MUSIC_TAGS), None)
    if music:
        return f"tag: {music}"
    return "" if any(t in NOT_MUSIC_TAGS for t in tags) else None


# Words too generic to identify a place ("Salle du Lavoir, Lyon" -> "lavoir").
GENERIC_PLACE_WORDS = {
    *("le la les l du de des d au aux the et en".split()),
    *("salle club bar theatre lyon villeurbanne france".split()),
    # rooms inside a venue ("Grande salle", "Studio"): never a separate place
    *("grande grand petite petit studio amphi amphitheatre scene foyer".split()),
}
ADDRESS_RE = re.compile(
    r"^\s*\d|\b(rue|avenue|av|boulevard|bd|quai|cours|chemin|place|all[ée]e|impasse"
    r"|mont[ée]e)\b",
    re.IGNORECASE,
)
APOSTROPHES = re.compile("[\u2019\u2018\u02bc]")  # ’ ‘ ʼ
MIN_PLACE_KEY = 4  # shorter keys are too vague to name a place
MIN_PARTIAL_KEY = 6  # a partial match ("Bourse du Travail" in a longer name) needs more


def place_tokens(name: str) -> tuple[str, ...]:
    """Normalised words of a place name, without accents or generic/room words."""
    # typographic apostrophes separate words like "'" (L’Épicerie = L'Épicerie); stripping
    # them as non-ASCII would glue "l" to the next word ("lepicerie")
    name = APOSTROPHES.sub(" ", name)
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return tuple(t for t in re.split(r"[^a-z0-9]+", text) if t and t not in GENERIC_PLACE_WORDS)


def place_key(name: str) -> str:
    return "".join(place_tokens(name))


def _names_match(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Same place: equal keys, or the shorter name's words appear in a row in the longer."""
    short, long = sorted((a, b), key=lambda t: len("".join(t)))
    key = "".join(short)
    if len(key) < MIN_PLACE_KEY:
        return False
    if short == long:
        return True
    if len(key) < MIN_PARTIAL_KEY:
        return False
    n = len(short)
    return any(long[i : i + n] == short for i in range(len(long) - n + 1))


def is_excluded_place(location: str, excluded_keys: list[tuple[str, ...]]) -> bool:
    """The location names an excluded venue: its words appear in a row in the location."""
    loc = place_tokens(location)
    return any(
        _names_match(loc, ex) and len("".join(ex)) <= len("".join(loc)) for ex in excluded_keys
    )


def best_venue_match(
    tokens: tuple[str, ...], keys: dict[str, tuple[str, ...]], prefer: str | None = None
) -> str | None:
    """Id of the known venue whose name matches `tokens` (_names_match): exact name first,
    then the closest length; ties go to `prefer`. None when no name matches."""
    key = "".join(tokens)
    matches = [vid for vid, vkey in keys.items() if _names_match(tokens, vkey)]

    def rank(vid: str) -> tuple[bool, int, bool]:
        vkey = "".join(keys[vid])
        return (vkey != key, abs(len(vkey) - len(key)), vid != prefer)

    return min(matches, key=rank) if matches else None


def attribute_venue(
    event: RawEvent, venues: dict[str, Venue], keys: dict[str, tuple[str, ...]]
) -> tuple[str | None, str | None]:
    """(venue id, venue name if not a known venue) where the event actually takes place.

    Some venue sites list events held elsewhere (aggregators): the event's location
    wins over the page's venue when it clearly names another place.
    Ticketing-platform pages (promoters, tours) are stricter: an event is kept only
    without a location or at a known venue of the zone; otherwise the id is None (drop).
    """
    loc = event.location_name
    page_venue = venues.get(event.venue_id)
    # sources whose events carry their own venue are never re-attributed
    exempt = event.source == "ticketmaster" or event.source.startswith("gancio:")
    platform = event.source.startswith("platform:")
    if exempt or not loc or (ADDRESS_RE.search(loc) and not platform):
        return event.venue_id, None
    tokens = place_tokens(loc)
    key = "".join(tokens)
    if len(key) < MIN_PLACE_KEY and not platform:  # a room ("Grande salle") or a city
        return event.venue_id, None
    if (match := best_venue_match(tokens, keys, prefer=event.venue_id)) is not None:
        return match, None
    if platform:
        return None, None  # e.g. a tour date elsewhere listed on a promoter's page
    if page_venue is not None and page_venue.is_music_venue:
        return event.venue_id, None  # most likely one of its own rooms or stages
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
    stats: dict | None = None,
    excluded: tuple[str, ...] = (),
) -> list[Concert]:
    """Concerts in the window, duplicates across sources merged (see dedup.py).

    If `stats` is given, it receives the de-duplication counts for the run report.
    """
    found: list[Concert] = []
    keys = {vid: place_tokens(v.name) for vid, v in venues.items()}
    excluded_keys = [place_tokens(x) for x in excluded]
    for ev in raw:
        if not in_window(ev, now, window_days):
            continue
        if ev.location_name and is_excluded_place(ev.location_name, excluded_keys):
            continue  # held at a venue the user excluded, even when listed on another page
        venue_id, place_name = attribute_venue(ev, venues, keys)
        if venue_id is None:  # platform event outside the zone's known venues
            continue
        venue = venues.get(venue_id)
        reason = tag_reason(ev.tags)
        if reason is None:
            reason = concert_reason(ev, venue)
        if not reason:
            continue
        found.append(
            Concert(
                id="",  # set once duplicates are merged
                title=ev.title,
                start=ev.start.astimezone(tz).isoformat(),
                venue_id=venue_id,
                venue_name=venue.name if venue else (place_name or ev.location_name or "?"),
                url=ev.url,
                ticket_url=ev.ticket_url,
                performers=ev.performers,
                sources=[ev.source],
                reason=reason,
                links=make_links(ev.url, ev.ticket_url, ev.source),
            )
        )
    concerts, dedup_stats = dedupe(found, venues)
    if stats is not None:
        stats.update(dedup_stats)
    return sorted(concerts, key=lambda c: (c.start, c.venue_name, c.title))
