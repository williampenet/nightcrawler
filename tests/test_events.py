from datetime import datetime

from nightcrawler.events import build_concerts, concert_reason, place_key
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
