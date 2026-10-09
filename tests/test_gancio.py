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
from nightcrawler.venues import merge

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
    # first the untagged events whose title does not decide, nearest first; then every
    # other event, whose text is what the taste judge reads (WIP-91), each once
    paths = [c.request.url.path for c in detail.calls]
    assert paths[:2] == [
        "/api/event/detail/dazzlingkillmen-us-pord-comte-zero",
        "/api/event/detail/atelier-velo",
    ]
    assert len(paths) == len(set(paths)) == sum(1 for e in events if "/event/" in (e.url or ""))
    assert all(e.description == "Soirée noise rock" for e in events if "/event/" in (e.url or ""))
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
    # Grenoble is outside the zone: a likely mismatch, the event stays without a venue
    assert [e.title for e in events] == ["A", "B", "C", "D"]
    assert route.call_count == 3  # same address asked once (whitespace normalised)
    assert (geocode.calls, geocode.found) == (3, 2)


def test_geocoder_is_capped():
    class NoFetch:
        def get(self, *a, **k):
            raise AssertionError("over the cap")

    geocode = gancio.Geocoder(NoFetch(), limit=0)
    assert geocode("1 rue X") is None


@respx.mock
def test_geocoder_survives_bad_answers(zone):
    respx.get("https://data.geopf.fr/robots.txt").respond(404)
    answers = iter(
        [
            httpx.Response(500),
            httpx.Response(200, text="not json"),
            httpx.Response(200, json={"features": "x"}),
            httpx.Response(200, json={"features": [{"properties": {"score": 0.9}}]}),
            httpx.Response(
                200,
                json={
                    "features": [
                        {"geometry": {"coordinates": [4.8, 45.7]}, "properties": {"score": 0.3}}
                    ]
                },
            ),
            httpx.Response(
                200,
                json={
                    "features": [
                        {"geometry": {"coordinates": ["nan", 45.7]}, "properties": {"score": 0.9}}
                    ]
                },
            ),
        ]
    )
    route = respx.get(gancio.GEOCODER_URL).mock(side_effect=lambda r: next(answers))
    geocode = gancio.Geocoder(Fetcher(cache_dir=None, min_interval=0), near=(45.75, 4.83))
    assert [geocode(f"{i} rue X") for i in range(6)] == [None] * 6
    assert route.calls[0].request.url.params["lat"] == "45.75"


@respx.mock
def test_places_at_null_island_are_unknown_not_out_of_zone(zone, tz):
    # WIP-58: agenda.villemorte.fr stores Grrrnd Zero at latitude 0, longitude 0
    gz_address = "60, avenue de Bohlen 69120 Vaulx-en-Velin"

    def ban(request):
        coords = {gz_address: [4.9183, 45.7695]}.get(request.url.params["q"])
        feats = [{"geometry": {"coordinates": coords}, "properties": {"score": 0.9}}]
        return httpx.Response(200, json={"features": feats if coords else []})

    respx.get("https://data.geopf.fr/robots.txt").respond(404)
    route = respx.get(gancio.GEOCODER_URL).mock(side_effect=ban)
    gz = {"id": 10, "name": "Grrrnd Zero", "address": gz_address}
    items = [
        _item("gz-int", {**gz, "latitude": 0, "longitude": 0}),
        _item("gz-str", {**gz, "latitude": "0.0", "longitude": "0"}),
        _item(
            "nan",
            {
                "id": 11,
                "name": "Sans point",
                "address": "nulle part",
                "latitude": "nan",
                "longitude": 4.85,
            },
        ),
        _item("inf", {"id": 12, "name": "Infini", "latitude": "inf", "longitude": 4.85}),
        # a real point outside the zone (Annecy) is still dropped, never geocoded
        _item(
            "annecy",
            {"id": 13, "name": "Loin", "address": gz_address, "latitude": 45.9, "longitude": 6.12},
        ),
        # one coordinate at 0 is a real point (Greenwich meridian): outside the zone
        _item(
            "meridian",
            {
                "id": 14,
                "name": "Méridien",
                "address": gz_address,
                "latitude": 45.75,
                "longitude": 0,
            },
        ),
    ]
    geocode = gancio.Geocoder(Fetcher(cache_dir=None, min_interval=0))
    venues, events = gancio.parse(items, BASE, zone, _now(tz), tz, geocode)
    assert [e.title for e in events] == ["Gz-Int", "Gz-Str", "Nan", "Inf"]
    # kept and attributed to Grrrnd Zero at the geocoded address, never at (0, 0)
    assert [(v.id, v.name) for v in venues] == [("gancio:agenda.example:10", "Grrrnd Zero")]
    assert (venues[0].latitude, venues[0].longitude) == (45.7695, 4.9183)
    assert {e.venue_id for e in events[:2]} == {"gancio:agenda.example:10"}
    # unknown and not found: event kept without a venue, no invented position
    assert [e.location_name for e in events[2:]] == ["Sans point", "Infini"]
    assert route.call_count == 2  # GZ address once (memoised) + "nulle part"
    # merged with the same venue found on the map, so its events land there
    osm_gz = Venue("osm:node/1", "Grrrnd Zero", 45.7696, 4.9184, "music_venue")
    _, alias = merge([[osm_gz], venues])
    assert alias["gancio:agenda.example:10"] == "osm:node/1"


def test_null_island_without_geocoder_keeps_event_without_venue(zone, tz):
    place = {"id": 10, "name": "Grrrnd Zero", "address": "Lyon", "latitude": 0, "longitude": 0}
    venues, events = gancio.parse([_item("gz", place)], BASE, zone, _now(tz), tz)
    assert venues == [] and [e.location_name for e in events] == ["Grrrnd Zero"]


def test_bool_is_not_a_coordinate():
    assert [gancio._coord(v) for v in (True, False, 1, "45.7")] == [None, None, 1.0, 45.7]


def test_detail_order_cap_and_skips(tz, monkeypatch):
    """WIP-91: the cap keeps the events whose kind needs the text first; events with a
    description or without a Gancio event link are never fetched."""

    def ev(title, day, tags=(), url="https://g.example/event/x", description=None):
        start = datetime(2026, 10, day, 20, tzinfo=tz)
        return RawEvent(title=title, start=start, source="gancio:g", venue_id="v", url=url,
                        tags=list(tags), description=description)  # fmt: skip

    later_unsure = ev("Rencontre", 20)  # title does not decide: needs the text to be kept
    soon_tagged = ev("Live", 6, tags=["concert"])
    soon_concert = ev("Concert punk", 7)
    has_text = ev("Concert", 5, description="déjà là")
    no_link = ev("Concert", 5, url="https://g.example/place/1")
    order = gancio.detail_order([soon_tagged, has_text, later_unsure, no_link, soon_concert])
    assert order == [later_unsure, soon_tagged, soon_concert]
    monkeypatch.setattr(gancio, "MAX_DETAILS", 2)
    assert gancio.detail_order([soon_tagged, later_unsure, soon_concert]) == [
        later_unsure, soon_tagged]  # fmt: skip
