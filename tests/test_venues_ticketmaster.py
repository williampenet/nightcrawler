import json
from datetime import datetime

import httpx
import respx

from nightcrawler.http import Fetcher
from nightcrawler.models import Venue
from nightcrawler.sources import ticketmaster
from nightcrawler.venues import merge


def test_merge_same_place_same_name():
    a = Venue(
        "osm:node/1", "Le Petit Bulbe", 45.75, 4.85, "music_venue", website="https://b.example"
    )
    b = Venue("tm:V1", "Petit Bulbe", 45.7501, 4.8501, "music_venue", sources=["ticketmaster"])
    c = Venue("tm:V2", "Petit Bulbe", 45.80, 4.90, "music_venue")  # same name, far away
    merged, alias = merge([[a], [b, c]])
    assert [v.id for v in merged] == ["osm:node/1", "tm:V2"]
    assert alias["tm:V1"] == "osm:node/1"
    assert "ticketmaster" in merged[0].sources


def test_ticketmaster_parse(fixture_text, tz):
    venues, events = ticketmaster.parse(json.loads(fixture_text("ticketmaster.json")), tz)
    assert [v.id for v in venues] == ["tm:V1"]
    assert venues[0].website is None and venues[0].address == "1 rue X, Lyon"
    assert len(events) == 1
    assert events[0].performers == ["Big Band"] and events[0].types == ["MusicEvent"]


def test_ticketmaster_skipped_without_key(zone, tz, monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    assert ticketmaster.collect(zone, None, datetime.now(tz), tz) == ([], [], "skipped")


def test_ticketmaster_params(zone, tz):
    p = ticketmaster.params_for(zone, "k", datetime(2026, 10, 5, 12, tzinfo=tz), 0)
    assert p["latlong"] == "45.7578,4.832" and p["unit"] == "km"
    assert p["startDateTime"] == "2026-10-05T10:00:00Z"
    assert p["classificationName"] == "music"
    assert p["locale"] == "*"  # default "en" returned 0 events for Lyon (WIP-26)


@respx.mock
def test_ticketmaster_failure_does_not_stop(zone, tz, monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "k")
    respx.get(ticketmaster.API_URL).mock(side_effect=httpx.ConnectError("down"))
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    assert ticketmaster.collect(zone, fetcher, datetime.now(tz), tz) == (
        [],
        [],
        "error: ConnectError",
    )


def test_merge_identical_name_up_to_1500m():
    # coordinates measured on the 2026-10-06 run: same hall, 580 m apart (WIP-51)
    osm_v = Venue("osm:way/85000536", "Le Transbordeur", 45.78397, 4.86088, "music_venue")
    tm_v = Venue("tm:rZ6SnyZ6A6", "LE TRANSBORDEUR", 45.778753, 4.859536, "music_venue")
    merged, alias = merge([[osm_v], [tm_v]])
    assert [v.id for v in merged] == ["osm:way/85000536"]
    assert alias["tm:rZ6SnyZ6A6"] == "osm:way/85000536"


def test_merge_far_or_partial_or_generic_names_stay_apart():
    a = Venue("osm:1", "Le Transbordeur", 45.78397, 4.86088, "music_venue")
    far = Venue("tm:1", "Le Transbordeur", 45.80, 4.86088, "music_venue")  # ~1.8 km
    partial = Venue("tm:2", "Transbordeur Café", 45.7795, 4.8600, "music_venue")  # ~500 m
    fetes1 = Venue("osm:2", "Salle des fêtes", 45.70, 4.80, "music_venue")
    fetes2 = Venue("osm:3", "Salle des fêtes", 45.705, 4.80, "music_venue")  # ~560 m
    merged, _ = merge([[a, fetes1], [far, partial, fetes2]])
    assert len(merged) == 5


def test_excluded_venue_names():
    from nightcrawler.venues import is_excluded

    excluded = ("Radiant Bellevue", "Toï Toï le Zinc")
    assert is_excluded(Venue("tm:x", "RADIANT-BELLEVUE", 45.8, 4.8, "music_venue"), excluded)
    assert is_excluded(Venue("g:x", "Toi Toi Le Zinc", 45.8, 4.8, "music_venue"), excluded)
    assert not is_excluded(Venue("o:x", "Le Transbordeur", 45.8, 4.8, "music_venue"), excluded)
