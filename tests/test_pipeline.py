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
        json={"data": [{"id": 77, "name": "Sunn & Co", "nb_fan": 5000}]}
    )
    respx.get("https://api.deezer.com/artist/77/related").respond(
        json={"data": [{"name": "Boris"}]}
    )
    respx.get("https://musicbrainz.org/ws/2/artist").respond(
        json={"artists": [{"name": "Sunn & Co", "score": 100, "tags": []}]}
    )
    respx.get("https://ombres.example/robots.txt").respond(404)
    respx.get(host="ombres.example", path="/").respond(200, html=fixture_text("microdata.html"))

    now = datetime(2026, 10, 5, 12, tzinfo=tz)
    ref = tmp_path / "ref.csv"  # reference coverage wiring (WIP-55)
    ref.write_text("date,artists,venue\n2026-10-10,Nobody,Le Petit Bulbe\n", encoding="utf-8")
    report = run(zone, tmp_path, Fetcher(cache_dir=None, min_interval=0), now=now, reference=ref)

    assert (report["coverage"]["in_window"], report["coverage"]["found"]) == (1, 0)
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
    assert report["store"] == {"status": "off"}  # no database_url: stateless, as before


@respx.mock
def test_end_to_end_excluded_venue(tmp_path, zone, tz, fixture_text, monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    respx.post(OVERPASS_URL).respond(200, text=fixture_text("overpass.json"))
    respx.get("https://bulbe.example/robots.txt").respond(404)
    respx.get(host="bulbe.example", path="/").respond(200, html=fixture_text("home.html"))
    respx.get("https://bulbe.example/programmation/").respond(200, html=fixture_text("agenda.html"))
    respx.get("https://api.deezer.com/search/artist").respond(
        json={"data": [{"id": 77, "name": "Sunn & Co", "nb_fan": 5000}]}
    )
    respx.get("https://api.deezer.com/artist/77/related").respond(
        json={"data": [{"name": "Boris"}]}
    )
    respx.get("https://musicbrainz.org/ws/2/artist").respond(
        json={"artists": [{"name": "Sunn & Co", "score": 100, "tags": []}]}
    )
    respx.get("https://ombres.example/robots.txt").respond(404)
    respx.get(host="ombres.example", path="/").respond(200, html=fixture_text("microdata.html"))

    now = datetime(2026, 10, 5, 12, tzinfo=tz)
    zone = replace(zone, excluded_venues=("Petit Bulbe",))
    ref = tmp_path / "ref.csv"  # no venue column: the coverage measure fails, the run does not
    ref.write_text("date,artists\n2026-10-10,Nobody\n", encoding="utf-8")
    report = run(zone, tmp_path, Fetcher(cache_dir=None, min_interval=0), now=now, reference=ref)

    assert report["coverage"] == {"status": "error: KeyError"}
    assert "| Reference events found (FR-11) | error: KeyError |" in summary_markdown(report)
    assert report["venues"] == 2 and report["venues_excluded"] == 1
    concerts = json.loads((tmp_path / "data/concerts.json").read_text())
    assert concerts == []  # "Drone Night" was at Le Petit Bulbe
    assert "Le Petit Bulbe" not in (tmp_path / "data/venues.json").read_text()


def test_app_config_only_public_keys(tmp_path):
    from nightcrawler.cli import write_app_config

    src = tmp_path / "app.yaml"
    src.write_text(
        'spotify_client_id: "0123456789abcdef0123456789abcdef"\nsecret: x\n'
        'feedback_url: "https://f.example/"\n'
    )
    write_app_config(src, tmp_path / "app-config.json")
    out = json.loads((tmp_path / "app-config.json").read_text())
    assert out == {
        "spotify_client_id": "0123456789abcdef0123456789abcdef",
        "feedback_url": "https://f.example/",
    }
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
    assert report["sources"]["priority_venues"] == []  # none configured in this zone
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
    assert "platforms=- |" in one_line(report)
    assert "store=off |" in one_line(report)


@respx.mock
def test_end_to_end_platform_pages(tmp_path, zone, tz, fixture_text, monkeypatch):
    from nightcrawler.cli import one_line

    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    respx.post(OVERPASS_URL).respond(200, text=fixture_text("overpass.json"))
    respx.get("https://bulbe.example/robots.txt").respond(404)
    respx.get(host="bulbe.example", path="/").respond(200, html=fixture_text("home.html"))
    respx.get("https://bulbe.example/programmation/").respond(200, html=fixture_text("agenda.html"))
    # the theatre's site has no agenda, only links to ticketing platforms
    respx.get("https://ombres.example/robots.txt").respond(404)
    respx.get(host="ombres.example", path="/").respond(
        200, html=fixture_text("platform_venue.html")
    )
    respx.get("https://shotgun.live/robots.txt").respond(404)
    respx.get("https://shotgun.live/fr/venues/theatre-des-ombres").respond(
        200, html=fixture_text("shotgun_venue.html")
    )
    respx.get("https://dice.fm/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /venue/\n", headers={"content-type": "text/plain"}
    )
    # dice is blocked, so the third link gets the venue's second slot
    respx.get("https://www.helloasso.com/robots.txt").respond(404)
    # a JS app page: no event read, its format is reported (WIP-89)
    respx.get(host="www.helloasso.com").respond(
        200, html='<script id="__NEXT_DATA__" type="application/json">{}</script><p>Saison</p>'
    )
    respx.get(host="api.deezer.com").respond(json={"data": []})
    respx.get(host="musicbrainz.org").respond(json={"artists": []})

    now = datetime(2026, 10, 5, 12, tzinfo=tz)
    report = run(zone, tmp_path, Fetcher(cache_dir=None, min_interval=0), now=now)

    assert report["probe_status"] == {"structured": 2, "no_website": 1}
    assert report["probe_method"] == {"json-ld": 1, "platform:shotgun": 1}
    assert report["sources"]["platforms"] == {
        "dice": {"pages": 0, "with_events": 0, "events": 0, "robots_blocked": 1,
                 "budget_skipped": 0, "formats": {}},
        "helloasso": {"pages": 1, "with_events": 0, "events": 0, "robots_blocked": 0,
                      "budget_skipped": 0, "formats": {"next_data": 1}},
        "shotgun": {"pages": 1, "with_events": 1, "events": 2, "robots_blocked": 0,
                    "budget_skipped": 0, "formats": {}},
    }  # fmt: skip
    line = one_line(report)
    assert "platforms=dice(pages=0 with_events=0 events=0 robots_blocked=1 budget_skipped=0 " \
        "formats=-),helloasso(" in line  # fmt: skip
    assert "budget_skipped=0 formats=next_data*1)" in line
    # what the judge can read (WIP-89): no store in this run, so no description counts
    assert report["content"]["descriptions"] == "off"
    assert set(report["content"]["all"]) == {"concerts", "lineup", "identified"}
    assert report["content"]["all"]["concerts"] == report["concerts"]
    assert "platform:shotgun" in report["content"]["by_source"]
    concerts = json.loads((tmp_path / "data/concerts.json").read_text())
    by_title = {c["title"]: c for c in concerts}
    assert by_title["Kraut Tuesday"]["venue_name"] == "Théâtre des Ombres"
    assert by_title["Kraut Tuesday"]["sources"] == ["platform:shotgun"]
    # the event's location names another known venue: attribution (WIP-35) moves it
    assert by_title["Bulbe Session"]["venue_name"] == "Le Petit Bulbe"
