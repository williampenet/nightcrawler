from datetime import datetime

from nightcrawler.events import (
    attribute_venue,
    build_concerts,
    concert_reason,
    place_key,
    place_tokens,
)
from nightcrawler.models import RawEvent, Venue
from nightcrawler.structured import jsonld_events

MUSIC = Venue("m", "Le Petit Bulbe", 45.75, 4.85, "music_venue")
THEATRE = Venue("t", "Théâtre des Ombres", 45.76, 4.83, "theatre")


def ev(title, day=10, venue="m", source="json-ld", types=(), hour=20, **kw):
    return RawEvent(
        title=title,
        start=datetime.fromisoformat(f"2026-10-{day:02d}T{hour:02d}:00:00+02:00"),
        source=source,
        venue_id=venue,
        types=list(types),
        **kw,
    )


def test_concert_reason():
    assert concert_reason(ev("X", types=["MusicEvent"]), THEATRE) == "schema.org type"
    assert concert_reason(ev("Soirée"), MUSIC) == "music venue"
    assert concert_reason(ev("Atelier sérigraphie"), MUSIC) is None
    assert concert_reason(ev("Concert de jazz", venue="t"), THEATRE) == "music keywords"
    assert concert_reason(ev("Hamlet", venue="t"), THEATRE) is None


def test_build_concerts_window_dedupe(tz):
    now = datetime(2026, 10, 5, 12, tzinfo=tz)
    raw = [
        ev("Drone Night", url="https://a.example"),
        ev("DRONE  night!", source="ical", ticket_url="https://t.example"),  # same concert
        ev("Old show", day=1),  # in the past
        ev("Hamlet", venue="t"),  # not music
        ev("Concert de jazz", day=12, venue="t"),
    ]
    concerts = build_concerts(raw, {"m": MUSIC, "t": THEATRE}, now=now, window_days=60, tz=tz)
    assert [c.title for c in concerts] == ["Drone Night", "Concert de jazz"]
    drone = concerts[0]
    assert drone.sources == ["json-ld", "ical"]
    assert drone.url == "https://a.example" and drone.ticket_url == "https://t.example"
    assert drone.venue_name == "Le Petit Bulbe"


def test_window_upper_bound(tz):
    now = datetime(2026, 10, 5, 12, tzinfo=tz)
    far = ev("Far", day=30)
    assert build_concerts([far], {"m": MUSIC}, now=now, window_days=10, tz=tz) == []


def test_concert_reason_other_art_forms():
    assert concert_reason(ev("Impro libre #4", venue="t"), THEATRE) is None
    assert concert_reason(ev("Match d'improvisation"), MUSIC) is None
    assert concert_reason(ev("Paul Mirabel", description="Seul en scène"), MUSIC) is None
    assert concert_reason(ev("Stand-up night"), MUSIC) is None
    assert concert_reason(ev("Exposition photo"), MUSIC) is None
    assert concert_reason(ev("Hamlet", types=["TheaterEvent"]), MUSIC) is None
    # a strong music signal wins
    assert concert_reason(ev("Atelier + concert"), MUSIC) == "music venue"
    assert concert_reason(ev("Impro jazz", venue="t"), THEATRE) == "music keywords"
    assert concert_reason(ev("Humour", types=["MusicEvent"]), THEATRE) == "schema.org type"
    # "théâtre" in a description is often just the venue name
    assert concert_reason(ev("Laura Cahen", description="Au Théâtre"), MUSIC) == "music venue"
    # activities: only a performance word keeps them; "live" is a weak signal
    for title in ("Atelier DJ", "Atelier chorale", "Conférence : histoire du jazz", "Expo pop-up"):
        assert concert_reason(ev(title), MUSIC) is None, title
    assert concert_reason(ev("One man show, spectacle en live"), MUSIC) is None
    assert concert_reason(ev("Conférence", description="suivie d'un concert"), MUSIC)
    assert concert_reason(ev("Live à la Halle", venue="t"), THEATRE) == "music keywords"


BOURSE = Venue("b", "Bourse du Travail", 45.76, 4.85, "arts_centre")
TRANSBO = Venue("x", "Transbordeur", 45.78, 4.86, "music_venue")


def test_place_key():
    assert place_key("Salle Albert Thomas - Bourse du Travail") == "albertthomasboursetravail"
    assert place_key("Le Transbordeur, Lyon") == "transbordeur"


def test_venue_attribution_from_location(fixture_text, tz):
    raw = jsonld_events(fixture_text("aggregator.html"), "b", tz)
    raw.append(ev("Ghinzu", day=14, venue="x", source="ticketmaster"))
    now = datetime(2026, 10, 5, 12, tzinfo=tz)
    venues = {"b": BOURSE, "x": TRANSBO, "m": MUSIC}
    concerts = build_concerts(raw, venues, now=now, window_days=60, tz=tz)
    got = {c.title: (c.venue_id, c.venue_name) for c in concerts}
    assert got == {
        "Ghinzu": ("x", "Transbordeur"),  # another known venue
        "Orchestre national": ("b", "Bourse du Travail"),  # the page's own venue
        "Gaël Faye": ("place:halletonygarnier", "Halle Tony Garnier"),  # unknown place
        "Kery James": ("b", "Bourse du Travail"),  # an address, not a place name
    }
    ghinzu = next(c for c in concerts if c.title == "Ghinzu")
    assert ghinzu.sources == ["json-ld", "ticketmaster"]  # merged with the venue's own listing


OPERA = Venue("o", "Opéra de Lyon", 45.76, 4.84, "theatre")
UNDERGROUND = Venue("u", "Opéra Underground", 45.76, 4.84, "music_venue")
SONIC = Venue("s", "Sonic", 45.74, 4.82, "music_venue")
CAVE = Venue("c", "La Cave", 45.75, 4.83, "bar")


def where(location, page="o", source="json-ld"):
    venues = {v.id: v for v in (OPERA, UNDERGROUND, SONIC, CAVE, BOURSE, TRANSBO, MUSIC)}
    keys = {vid: place_tokens(v.name) for vid, v in venues.items()}
    event = ev("X", venue=page, source=source, location_name=location)
    return attribute_venue(event, venues, keys)


def test_attribution_rooms_stay_at_page_venue():
    for room in ("Grande salle", "Studio", "Amphithéâtre", "Petite scène", "Foyer"):
        assert where(room) == ("o", None), room
        assert where(room, page="m") == ("m", None), room


def test_attribution_prefers_exact_then_closest_name():
    assert where("Opéra de Lyon", page="b") == ("o", None)
    assert where("Opéra Underground", page="b") == ("u", None)
    assert where("Salle Albert Thomas - Bourse du Travail", page="b") == ("b", None)


def test_attribution_negative_cases():
    # whole words only, and a short name never matches part of a longer one
    assert where("Supersonic Records", page="b")[0] == "place:supersonicrecords"
    assert where("La Cave des Voyageurs", page="b")[0] == "place:cavevoyageurs"
    # an unknown place listed by a music venue is most likely one of its own spaces
    assert where("Le Club Privé", page="m") == ("m", None)
    # addresses, cities, missing locations and ticketing events are left alone
    for loc in ("Place Bellecour", "3 allée des Arts", "Montée de la Grande Côte", "Lyon", None):
        assert where(loc, page="b") == ("b", None), loc
    assert where("Transbordeur", page="b", source="ticketmaster") == ("b", None)


def test_attribution_platform_events_stay_in_zone(tz):
    # a promoter's Shotgun page linked by a music venue lists its whole tour
    p = "platform:shotgun"
    assert where("Le Bataclan, Paris", page="m", source=p) == (None, None)
    assert where("Le Club Privé", page="m", source=p) == (None, None)  # no own-room fallback
    assert where("3 allée des Arts", page="m", source=p) == (None, None)
    assert where(None, page="m", source=p) == ("m", None)
    assert where("Le Petit Bulbe", page="m", source=p) == ("m", None)
    assert where("Transbordeur", page="m", source=p) == ("x", None)  # known zone venue
    now = datetime(2026, 10, 5, 12, tzinfo=tz)
    raw = [
        ev("Tour Paris", types=["MusicEvent"], source=p, location_name="Le Bataclan, Paris"),
        ev("Tour Lyon", types=["MusicEvent"], source=p, location_name="Le Petit Bulbe"),
    ]
    concerts = build_concerts(raw, {"m": MUSIC}, now=now, window_days=60, tz=tz)
    assert [c.title for c in concerts] == ["Tour Lyon"]
