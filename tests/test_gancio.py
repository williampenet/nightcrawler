import json
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import respx

from nightcrawler.config import load_zone
from nightcrawler.events import build_concerts, tag_reason
from nightcrawler.http import Fetcher
from nightcrawler.models import RawEvent, Venue
from nightcrawler.sources import gancio

BASE = "https://agenda.example"


def _zone(zone):
    return replace(zone, gancio_instances=({"name": "Test agenda", "url": BASE + "/"},))


def _now(tz):
    return datetime(2026, 10, 5, 12, tzinfo=tz)


def test_parse_keeps_zone_and_window(zone, tz, fixture_text):
    items = json.loads(fixture_text("gancio_events.json"))
    venues, events = gancio.parse(items, BASE, zone, _now(tz), tz)
    titles = [e.title for e in events]
    assert "Concert au bord du lac" not in titles  # place outside the radius
    assert "Concert passé" not in titles  # before today
    assert "Place malformée" not in titles  # place is not an object
    assert len(events) == 8
    assert {v.name for v in venues} == {
        "Petit Bulbe",
        "Grrrnd Zero",
        "Théâtre de l’Elysée",
        "Sonic",
        "Les Clameurs",
    }
    by_title = {e.title: e for e in events}
    # no place id: derived from the name
    assert by_title["Kraut session"].venue_id == "gancio:agenda.example:name-les-clameurs"
    # empty place name: event kept, no venue
    assert by_title["Fanfare"].venue_id == "gancio:agenda.example:7"
    assert by_title["Fanfare"].location_name is None
    gz = next(v for v in venues if v.name == "Grrrnd Zero")
    assert gz.address.startswith("60, avenue de Bohlen") and gz.sources == ["gancio:agenda.example"]
    nuit = next(e for e in events if e.title == "Nuit club")
    assert nuit.tags == ["dj set"] and nuit.location_name == "Sonic"
    assert nuit.url == BASE + "/event/nuit-club" and nuit.source == "gancio:agenda.example"
    secret = next(e for e in events if e.title == "Les Mains Froides")
    assert secret.location_name == "Lieu tenu secret"  # no coordinates: kept, no venue


@respx.mock
def test_collect_fetches_details_only_when_needed(zone, tz, fixture_text):
    respx.get(BASE + "/robots.txt").respond(200, text="user-agent: *\nallow: /\n")
    listing = respx.get(BASE + "/api/events").respond(200, text=fixture_text("gancio_events.json"))
    detail = respx.get(url__startswith=BASE + "/api/event/detail/").respond(
        200, json={"description": "<p>Soirée noise rock</p>"}
    )
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    venues, events, status = gancio.collect(_zone(zone), fetcher, _now(tz), tz)
    assert status == "ok" and len(venues) == 5 and len(events) == 8
    # window filters: from the start of today to the end of the window, in unix seconds
    params = listing.calls[0].request.url.params
    assert params["start"] == str(int(datetime(2026, 10, 5, tzinfo=tz).timestamp()))
    assert params["end"] == str(int(datetime(2026, 12, 5, tzinfo=tz).timestamp()))
    # only untagged events whose title does not decide (tagged ones never), nearest first
    assert [c.request.url.path for c in detail.calls] == [
        "/api/event/detail/dazzlingkillmen-us-pord-comte-zero",
        "/api/event/detail/atelier-velo",
    ]
    dazz = next(e for e in events if e.title.startswith("DazzlingKillmen"))
    assert dazz.description == "Soirée noise rock"


@respx.mock
def test_collect_failure_does_not_stop(zone, tz):
    respx.get(BASE + "/robots.txt").respond(404)
    respx.get(BASE + "/api/events").respond(500)
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    result = gancio.collect(_zone(zone), fetcher, _now(tz), tz)
    assert result == ([], [], "error: Test agenda: HTTP 500")


def test_collect_skipped_without_instances(zone, tz):
    assert gancio.collect(zone, None, _now(tz), tz) == ([], [], "skipped")


def test_zone_config_lists_villemorte():
    zone = load_zone("config/zone.yaml")
    assert {"name": "Ville Morte", "url": "https://agenda.villemorte.fr"} in zone.gancio_instances
    assert zone.contains(45.7695, 4.9183) and not zone.contains(45.9, 6.12)


def test_tag_reason():
    assert tag_reason(["Concert"]) == "tag: concert"
    assert tag_reason(["théâtre", "musique live"]) == "tag: musique live"  # music wins
    assert tag_reason(["théâtre"]) == ""
    assert tag_reason(["soirée de soutien"]) is None and tag_reason([]) is None


def test_tags_drive_the_concert_filter(tz):
    now = _now(tz)
    club = Venue("v1", "Club", 45.75, 4.85, "music_venue")
    hall = Venue("v2", "Salle", 45.75, 4.85, "events_venue")

    def ev(title, venue_id, tags):
        return RawEvent(title, now, "gancio:x", venue_id, tags=tags)

    raw = [
        ev("Seule en scène", "v1", ["théâtre"]),  # tag beats the music venue
        ev("Les Mains Froides", "v2", ["concert"]),  # tag beats the venue category
        ev("Grosse soirée", "v1", []),  # untagged: existing rules (music venue)
        ev("Repas partagé", "v2", []),  # untagged, nothing musical
    ]
    concerts = build_concerts(raw, {"v1": club, "v2": hall}, now=now, window_days=60, tz=tz)
    assert {c.title: c.reason for c in concerts} == {
        "Les Mains Froides": "tag: concert",
        "Grosse soirée": "music venue",
    }


def _item(slug, place):
    start = int(datetime(2026, 10, 15, 20, tzinfo=ZoneInfo("Europe/Paris")).timestamp())
    return {
        "title": slug.title(),
        "slug": slug,
        "start_datetime": start,
        "tags": ["concert"],
        "place": place,
    }


@respx.mock
def test_places_without_coordinates_are_geocoded(zone, tz):
    def ban(request):
        q = request.url.params["q"]
        coords = {
            "4-6 place Hubert Mounier 69002 Lyon": [4.8223, 45.7425],
            "Grenoble": [5.7245, 45.1885],
        }.get(q)
        feats = [{"geometry": {"coordinates": coords}, "properties": {"score": 0.9}}]
        return httpx.Response(200, json={"features": feats if coords else []})

    respx.get("https://data.geopf.fr/robots.txt").respond(404)
    route = respx.get(gancio.GEOCODER_URL).mock(side_effect=ban)
    items = [
        _item(
            "a",
            {
                "id": 9,
                "name": "Marché Gare",
                "latitude": None,
                "longitude": None,
                "address": "4-6 place Hubert  Mounier\n69002 Lyon",
            },
        ),
        _item(
            "b",
            {
                "id": 9,
                "name": "Marché Gare",
                "latitude": None,
                "longitude": None,
                "address": "4-6 place Hubert Mounier 69002 Lyon",
            },
        ),
        _item("c", {"id": 10, "name": "Loin", "address": "Grenoble"}),
        _item("d", {"id": 11, "name": "Inconnu", "address": "nulle part"}),
    ]
    geocode = gancio.Geocoder(Fetcher(cache_dir=None, min_interval=0))
    venues, events = gancio.parse(items, BASE, zone, _now(tz), tz, geocode)
    assert [v.name for v in venues] == ["Marché Gare"]
    assert (round(venues[0].latitude, 4), round(venues[0].longitude, 4)) == (45.7425, 4.8223)
    assert [e.title for e in events] == ["A", "B", "D"]  # Grenoble is outside the zone
    assert route.call_count == 3  # same address asked once (whitespace normalised)
    assert (geocode.calls, geocode.found) == (3, 2)


def test_geocoder_is_capped():
    class NoFetch:
        def get(self, *a, **k):
            raise AssertionError("over the cap")

    geocode = gancio.Geocoder(NoFetch(), limit=0)
    assert geocode("1 rue X") is None
