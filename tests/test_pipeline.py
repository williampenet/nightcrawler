import json
from dataclasses import replace
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
    # "Drone Night" (MusicEvent) is kept; the workshop and "Impro libre #4"
    # (improvisation theatre, no music signal) are dropped.
    assert [c["title"] for c in concerts] == ["Drone Night"]
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


@respx.mock
def test_end_to_end_with_gancio(tmp_path, zone, tz, fixture_text, monkeypatch):
    from nightcrawler.cli import one_line

    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    respx.post(OVERPASS_URL).respond(200, text=fixture_text("overpass.json"))
    respx.get("https://bulbe.example/robots.txt").respond(404)
    respx.get(host="bulbe.example", path="/").respond(200, html=fixture_text("home.html"))
    respx.get("https://bulbe.example/programmation/").respond(200, html=fixture_text("agenda.html"))
    respx.get("https://ombres.example/robots.txt").respond(404)
    respx.get(host="ombres.example", path="/").respond(200, html=fixture_text("microdata.html"))
    respx.get(host="api.deezer.com").respond(json={"data": []})
    respx.get(host="musicbrainz.org").respond(json={"artists": []})
    respx.get("https://agenda.example/robots.txt").respond(404)
    respx.get("https://agenda.example/api/events").respond(
        200, text=fixture_text("gancio_events.json")
    )
    respx.get(url__startswith="https://agenda.example/api/event/detail/").respond(
        200, json={"description": "Noise rock"}
    )
    zone = replace(zone, gancio_instances=({"name": "Test", "url": "https://agenda.example"},))

    now = datetime(2026, 10, 5, 12, tzinfo=tz)
    report = run(zone, tmp_path, Fetcher(cache_dir=None, min_interval=0), now=now)

    # Petit Bulbe merges with the OSM venue; Grrrnd Zero, Elysée, Sonic, Les Clameurs are added
    assert report["venues"] == 7
    assert report["sources"]["gancio"] == {
        "status": "ok",
        "venues": 5,
        "events": 8,
        "instances": [{"name": "Test", "url": "https://agenda.example"}],
    }
    assert report["probe_method"]["gancio"] == 4
    concerts = json.loads((tmp_path / "data/concerts.json").read_text())
    # "Impro libre #4" is dropped (WIP-35), so are the workshop and the theatre play
    assert [c["title"] for c in concerts] == [
        "DazzlingKillmen (Us) + Pord + Comte Zero",
        "Drone Night",
        "Kraut session",
        "Fanfare",
        "Nuit club",
        "Les Mains Froides",
    ]
    drone = concerts[1]  # listed by the venue site and the agenda: kept once
    assert drone["sources"] == ["json-ld", "gancio:agenda.example"]
    assert concerts[-1]["venue_name"] == "Lieu tenu secret"
    assert "gancio=ok (venues=5 events=8)" in one_line(report)
