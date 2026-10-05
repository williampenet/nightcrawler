import json

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
