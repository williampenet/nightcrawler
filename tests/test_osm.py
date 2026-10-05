import json

import pytest
import respx

from nightcrawler.http import Fetcher
from nightcrawler.sources import osm


def test_query_uses_zone(zone):
    q = osm.build_query(zone)
    assert "around:15000,45.7578,4.832" in q
    assert "music_venue" in q and '"live_music"="yes"' in q


def test_parse(fixture_text):
    venues = osm.parse(json.loads(fixture_text("overpass.json")))
    assert [v.name for v in venues] == ["Le Petit Bulbe", "Théâtre des Ombres", "Bar à Sons"]
    bulbe, ombres, bar = venues
    assert bulbe.id == "osm:node/1" and bulbe.is_music_venue
    assert ombres.website == "https://ombres.example"  # scheme added
    assert ombres.latitude == 45.76  # way centre
    assert ombres.address == "3 Rue Neuve, Lyon"
    assert not ombres.is_music_venue
    assert bar.category == "live_music" and bar.website is None


@respx.mock
def test_discover_retries_then_mirror(zone, fixture_text):
    first = respx.post("https://a.example/api").respond(503)
    respx.post("https://b.example/api").respond(200, text=fixture_text("overpass.json"))
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    venues = osm.discover(
        zone, fetcher, mirrors=("https://a.example/api", "https://b.example/api"), backoff=0
    )
    assert first.call_count == 2 and len(venues) == 3
    sent = first.calls[0].request.headers
    assert sent["accept"] == "application/json" and "nightcrawler" in sent["referer"]


@respx.mock
def test_discover_all_fail(zone):
    respx.post("https://a.example/api").respond(400)
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    with pytest.raises(RuntimeError, match="HTTP 400"):
        osm.discover(zone, fetcher, mirrors=("https://a.example/api",), backoff=0)
