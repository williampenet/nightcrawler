import json
from datetime import datetime

import respx

from nightcrawler.http import Fetcher
from nightcrawler.pipeline import run, summary_markdown
from nightcrawler.sources.osm import OVERPASS_URL


@respx.mock
def test_end_to_end(tmp_path, zone, tz, fixture_text, monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    respx.post(OVERPASS_URL).respond(200, text=fixture_text("overpass.json"))
    respx.get("https://bulbe.example/robots.txt").respond(404)
    respx.get(host="bulbe.example", path="/").respond(200, html=fixture_text("home.html"))
    respx.get("https://bulbe.example/programmation/").respond(200, html=fixture_text("agenda.html"))
    respx.get("https://api.deezer.com/search/artist").respond(
        json={"data": [{"id": 77, "name": "Sunn & Co", "nb_fan": 12}]}
    )
    respx.get("https://api.deezer.com/artist/77/related").respond(
        json={"data": [{"name": "Boris"}]}
    )
    respx.get("https://musicbrainz.org/ws/2/artist").respond(json={"artists": []})
    respx.get("https://ombres.example/robots.txt").respond(404)
    respx.get(host="ombres.example", path="/").respond(200, html=fixture_text("microdata.html"))

    now = datetime(2026, 10, 5, 12, tzinfo=tz)
    report = run(zone, tmp_path, Fetcher(cache_dir=None, min_interval=0), now=now)

    assert report["venues"] == 3
    assert report["probe_status"] == {"structured": 2, "no_website": 1}
    concerts = json.loads((tmp_path / "data/concerts.json").read_text())
    # "Drone Night" (MusicEvent) and "Impro libre #4" (keyword at a theatre);
    # the workshop is dropped.
    assert [c["title"] for c in concerts] == ["Drone Night", "Impro libre #4"]
    venues = json.loads((tmp_path / "data/venues.json").read_text())
    assert {v["name"]: v["concerts"] for v in venues}["Le Petit Bulbe"] == 1
    assert "| Venues found | 3 |" in summary_markdown(report)
    assert report["sources"]["ticketmaster"]["status"] == "skipped"
    assert report["sources"]["openstreetmap_venues"] == 3
    assert report["artists"]["candidates"] >= 1  # "Sunn & Co" from the JSON-LD performer
    artists = json.loads((tmp_path / "data/artists.json").read_text())
    assert artists["sunnco"]["related"] == ["Boris"]
    assert concerts[0]["artists"] == ["sunnco"]


def test_app_config_only_public_keys(tmp_path):
    from nightcrawler.cli import write_app_config

    src = tmp_path / "app.yaml"
    src.write_text('spotify_client_id: "0123456789abcdef0123456789abcdef"\nsecret: x\n')
    write_app_config(src, tmp_path / "app-config.json")
    out = json.loads((tmp_path / "app-config.json").read_text())
    assert out == {"spotify_client_id": "0123456789abcdef0123456789abcdef"}
    write_app_config(tmp_path / "missing.yaml", tmp_path / "empty.json")
    assert json.loads((tmp_path / "empty.json").read_text()) == {}
