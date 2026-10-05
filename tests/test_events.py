from datetime import datetime

from nightcrawler.events import build_concerts, concert_reason
from nightcrawler.models import RawEvent, Venue

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
