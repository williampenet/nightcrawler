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
