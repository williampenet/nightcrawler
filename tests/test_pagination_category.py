"""Listing pagination and configured venues for "Mes salles" (WIP-64).

Measured on 2026-10-07: the Opéra Underground listing paginates server-side with `&page=2`
(13 new links), and no OSM venue is named "Opéra Underground", so its concerts fell back to a
`place:` venue and the keyword rule. The Opéra entry under test is the real config entry.
"""

from datetime import datetime
from pathlib import Path

import pytest
import respx

from nightcrawler.config import load_zone
from nightcrawler.events import build_concerts, place_tokens
from nightcrawler.http import Fetcher
from nightcrawler.models import RawEvent, Venue
from nightcrawler.sources import listing_jsonld
from nightcrawler.venues import (
    SAME_PLACE_METERS,
    _norm,
    attach_to_configured,
    configured_venue_ids,
    configured_venues,
    distance_m,
    merge,
)

ZONE = load_zone(Path(__file__).parents[1] / "config/zone.yaml")
OPERA = next(e for e in ZONE.priority_venues if e["name"] == "Opéra Underground")
LISTING = OPERA["reader"]["urls"][0]
SEASON = "https://www.opera-lyon.com/fr/programmation/saison-2026-2027/opera-underground"
EMPTY = "<html><body>no JSON-LD here</body></html>"


def _now(tz):
    return datetime(2026, 10, 7, 9, tzinfo=tz)


def _links(*slugs):
    return "".join(
        f'<a href="/fr/programmation/saison-2026-2027/opera-underground/{s}">x</a>' for s in slugs
    )


def _read(reader, tz):
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    return listing_jsonld.read(reader, "Opéra Underground", fetcher, _now(tz), tz, 60)


def test_with_param_keeps_the_existing_query():
    assert listing_jsonld.with_param(LISTING, "page", 2) == LISTING + "&page=2"
    assert listing_jsonld.with_param("https://a.example/agenda?page=1&x=", "page", 3) == (
        "https://a.example/agenda?x=&page=3"
    )
    assert listing_jsonld.with_param("https://a.example/agenda", "page", 1) == (
        "https://a.example/agenda?page=1"
    )


@respx.mock
def test_pagination_stops_when_a_page_brings_no_new_link(fixture_text, tz):
    respx.get("https://www.opera-lyon.com/robots.txt").respond(404)
    first = respx.get(LISTING).respond(200, html=fixture_text("listing_jsonld/opera_listing.html"))
    page2 = respx.get(LISTING + "&page=2").respond(200, html=_links("page-two-a", "quatuor-bela"))
    page3 = respx.get(LISTING + "&page=3").respond(200, html=_links("page-two-a"))  # repeat
    details = respx.get(url__startswith=SEASON).respond(200, html=EMPTY)
    _, pages, status = _read(OPERA["reader"], tz)
    assert first.call_count == page2.call_count == page3.call_count == 1  # no &page=4
    assert [c.request.url.path.rsplit("/", 1)[-1] for c in details.calls] == [
        "leila-martial-elie-dufour-karma-bazar",
        "quatuor-bela",
        "page-two-a",  # the only new link of page 2
    ]
    assert (pages, status) == (6, "no_events")


@respx.mock
def test_pagination_cap_and_listing_errors_are_reported(tz):
    respx.get("https://v.example/robots.txt").respond(404)
    # a route without a query also matches any query: the paginated one goes first
    respx.get("https://v.example/agenda/202610?p=1").respond(200, html='<a href="/e/2">x</a>')
    respx.get("https://v.example/agenda/202610").respond(200, html='<a href="/e/1">x</a>')
    respx.get("https://v.example/agenda/202611").respond(404)  # month not published yet
    respx.get("https://v.example/agenda/202612?p=1").respond(500)  # a later page breaks
    respx.get("https://v.example/agenda/202612").respond(200, html='<a href="/e/3">x</a>')
    respx.get(url__regex=r"https://v\.example/e/\d$").respond(200, html=EMPTY)
    reader = {
        "urls": ["https://v.example/agenda/{yyyymm}"],
        "include": "^/e/",
        "paginate": {"param": "p", "start": 1, "max": 1},
    }
    _, pages, status = _read(reader, tz)
    assert status == (
        "page_cap: https://v.example/agenda/202610; no_events; listing errors: 2 (HTTP 404)"
    )
    # 202611 (404) and 202612?p=1 (500) are the two errors
    assert pages == 6  # 202610, its ?p=1, 202612, then /e/1 /e/2 /e/3


def test_configured_venue_is_created_when_no_known_venue_matches(tz):
    building = Venue("osm:way/1", "Opéra de Lyon", 45.7676, 4.8361, "theatre")
    no_coords = {k: v for k, v in OPERA.items() if k != "coordinates_from"}
    added = configured_venues((no_coords,), [building])
    assert [(v.id, v.name, v.category, v.latitude, v.sources) for v in added] == [
        ("config:operaunderground", "Opéra Underground", "music_venue", None, ["config"])
    ]
    assert building.category == "theatre"  # "Opéra de Lyon" is not the configured name
    # without the configured venue the event falls back to place: and the keyword rule
    ev = RawEvent(
        "Adélaïde Ferrière",
        datetime(2026, 10, 15, 20, tzinfo=tz),
        "listing_jsonld:www.opera-lyon.com",
        "listing_jsonld:x",
        types=["Event"],
        location_name="Opéra Underground",
    )
    venues = {building.id: building}
    assert build_concerts([ev], venues, now=_now(tz), window_days=60, tz=tz) == []
    venues |= {v.id: v for v in added}
    tm = RawEvent("ADELAIDE FERRIERE", ev.start, "ticketmaster", building.id)
    stats: dict = {}
    concerts = build_concerts([ev, tm], venues, now=_now(tz), window_days=60, tz=tz, stats=stats)
    assert [(c.venue_id, c.reason, c.sources) for c in concerts] == [
        ("config:operaunderground", "music venue", ["listing_jsonld:www.opera-lyon.com"]),
        ("osm:way/1", "ticketing category: music", ["ticketmaster"]),  # no coordinates: apart
    ]
    assert stats["conflicts"] == 0  # a venue without coordinates is not "far away" either


def _ferriere(tz):
    start = datetime(2026, 10, 15, 20, tzinfo=tz)
    source = "listing_jsonld:www.opera-lyon.com"
    return RawEvent("Adélaïde Ferrière", start, source, source, location_name="Opéra Underground")


def test_coordinates_from_merges_with_the_building_listing(tz):
    building = Venue("osm:way/1", "Opéra de Lyon", 45.7676, 4.8361, "theatre")
    (venue,) = configured_venues((OPERA,), [building])  # the real config entry
    assert (venue.latitude, venue.longitude) == (45.7676, 4.8361)
    ev = _ferriere(tz)
    tm = RawEvent("ADELAIDE FERRIERE", ev.start, "ticketmaster", building.id)
    venues = {v.id: v for v in (building, venue)}
    stats: dict = {}
    concerts = build_concerts([ev, tm], venues, now=_now(tz), window_days=60, tz=tz, stats=stats)
    assert [(c.title, c.sources) for c in concerts] == [
        ("Adélaïde Ferrière", ["listing_jsonld:www.opera-lyon.com", "ticketmaster"])
    ]
    assert (stats["merged"], stats["conflicts"]) == (1, 0)


def test_coordinates_from_missing_or_overridden():
    other = Venue("osm:node/9", "Le Sonic", 45.74, 4.82, "music_venue")
    (missing,) = configured_venues((OPERA,), [other])  # no "Opéra de Lyon" known
    assert missing.latitude is None and missing.longitude is None
    explicit = OPERA | {"latitude": 45.76, "longitude": 4.83}
    (venue,) = configured_venues((explicit,), [other])
    assert (venue.latitude, venue.longitude) == (45.76, 4.83)


def test_known_venue_with_the_same_name_takes_the_category():
    known = Venue("osm:node/3", "Opera Underground", 45.76, 4.83, "theatre")
    other = Venue("osm:node/4", "Opéra Underground Club Annex", 45.76, 4.83, "bar")
    assert configured_venues((OPERA,), [known, other]) == []
    assert (known.category, other.category) == ("music_venue", "bar")
    plain = OPERA | {"category": None}
    plain.pop("category")
    added = configured_venues((plain | {"latitude": 45.7, "longitude": 4.8},), [])
    assert [(v.category, v.latitude, v.longitude) for v in added] == [("events_venue", 45.7, 4.8)]


READER = "{type: listing_jsonld, urls: ['https://v.example/'], include: x"


@pytest.mark.parametrize(
    "venue, reader, error",
    [
        ("category: stadium", "", "unknown category"),
        ("latitude: 45.7", "", "latitude and longitude"),
        ("{latitude: 45.7, longitude: east}", "", "latitude and longitude"),
        ("{latitude: 48.85, longitude: 2.35}", "", "outside the zone"),  # Paris
        ("coordinates_from: ''", "", "coordinates_from"),
        ("venue_id: ''", "", "venue_id"),
        ("", ", paginate: {param: page, start: -1, max: 2}", "paginate"),
        ("", ", paginate: {start: 1, max: 2}", "paginate"),
        ("", ", paginate: {param: page, start: 1, max: 0}", "paginate"),
    ],
)
def test_zone_config_checks_category_coordinates_and_paginate(tmp_path, venue, reader, error):
    lines = [f"    {f}\n" for f in venue.strip("{}").split(", ") if f]
    path = tmp_path / "zone.yaml"
    path.write_text(
        "name: T\nlatitude: 45.7\nlongitude: 4.8\nradius_km: 15\npriority_venues:\n  - name: V\n"
        + "".join(lines)
        + f"    reader: {READER}{reader}}}\n"
    )
    with pytest.raises(ValueError, match=error):
        load_zone(path)


def test_zone_config_opera_and_pagination_entries():
    readers = {e["name"]: e["reader"] for e in ZONE.priority_venues}
    assert OPERA["category"] == "music_venue" and "latitude" not in OPERA
    assert OPERA["coordinates_from"] == "Opéra de Lyon"
    assert readers["Opéra Underground"]["paginate"] == {"param": "page", "start": 2, "max": 5}
    # the Épicerie and Marché Gare "Afficher plus" links are ?page=1: pages count from 0
    for name in ("L'Épicerie Moderne", "Le Marché Gare"):
        assert readers[name]["paginate"]["start"] == 1 and len(readers[name]["urls"]) == 1


EPICERIE = next(e for e in ZONE.priority_venues if e["name"] == "L'Épicerie Moderne")


def _epicerie_event(title, tz, location="L'Épicerie Moderne"):
    source = "listing_jsonld:epiceriemoderne.com"
    start = datetime(2026, 10, 8, 19, 30, tzinfo=tz)
    return RawEvent(title, start, source, source, types=["Event"], location_name=location)


# Live venues.json of 2026-10-07 08:38: the same place twice. Longitudes are not in the
# measure; both get the same one, the case most favourable to a merge.
LON = 4.86


def _epicerie_twice():
    osm = Venue(
        "osm:node/523776298",
        "L'épicerie moderne Place René Lescot, 69320 Feyzin",
        45.6747674,
        LON,
        "concert_hall",
    )
    gancio = Venue("gancio:villemorte:1", "L’Épicerie Moderne", 45.673384, LON, "events_venue")
    return osm, gancio


def test_venue_id_attaches_reader_events_directly(tz):
    osm, gancio = _epicerie_twice()
    assert EPICERIE["venue_id"] == "osm:node/523776298"  # the real config entry
    assert configured_venues((EPICERIE,), [osm, gancio]) == []
    assert (osm.category, osm.latitude) == ("concert_hall", 45.6747674)  # nothing overridden
    ids, notes = configured_venue_ids((EPICERIE,), [osm, gancio])
    assert (ids, notes) == ({"L'Épicerie Moderne": "osm:node/523776298"}, {})
    events = [_epicerie_event(t, tz) for t in ("THE LEMON TWIGS", "TEMPLES")]
    attach_to_configured(events, ids)
    venues = {v.id: v for v in (osm, gancio)}
    concerts = build_concerts(events, venues, now=_now(tz), window_days=60, tz=tz)
    assert sorted((c.title, c.venue_id, c.reason) for c in concerts) == [
        ("TEMPLES", "osm:node/523776298", "music venue"),
        ("THE LEMON TWIGS", "osm:node/523776298", "music venue"),
    ]


def test_missing_venue_id_falls_back_to_the_name_with_a_note():
    _, gancio = _epicerie_twice()  # the OSM node is not in this run
    assert configured_venues((EPICERIE,), [gancio]) == []
    ids, notes = configured_venue_ids((EPICERIE,), [gancio])
    assert ids == {"L'Épicerie Moderne": "gancio:villemorte:1"}  # exact name
    assert notes == {"L'Épicerie Moderne": "venue_id osm:node/523776298 not found, matched by name"}


def test_exact_name_beats_a_longer_music_venue():
    exact = Venue("osm:node/7", "Le Sonic", 45.74, 4.82, "events_venue")
    longer = Venue("osm:node/8", "Sonic Music Hall", 45.74, 4.82, "concert_hall")
    entry = {"name": "Le Sonic", "venue": "Le Sonic", "reader": {}}
    assert configured_venues((entry,), [longer, exact]) == []
    assert configured_venue_ids((entry,), [longer, exact])[0] == {"Le Sonic": "osm:node/7"}


def test_probe_and_platform_events_stay_at_the_page_venue(tz):
    osm, gancio = _epicerie_twice()  # the Gancio duplicate is now an exact name match
    start = datetime(2026, 10, 19, 20, tzinfo=tz)
    events = [
        RawEvent("TEMPLES", start, "json-ld", osm.id, location_name="L'Épicerie Moderne"),
        RawEvent("GILDAA", start, "platform:shotgun", osm.id, location_name="L'Épicerie Moderne"),
    ]
    venues = {v.id: v for v in (osm, gancio)}
    concerts = build_concerts(events, venues, now=_now(tz), window_days=60, tz=tz)
    assert sorted((c.title, c.venue_id, c.reason) for c in concerts) == [
        ("GILDAA", "osm:node/523776298", "music venue"),
        ("TEMPLES", "osm:node/523776298", "music venue"),
    ]


def test_curly_apostrophe_in_merge_names_and_the_measured_distance():
    osm, gancio = _epicerie_twice()
    assert _norm("L’Épicerie Moderne") == _norm("L'Épicerie Moderne") == "epiceriemoderne"
    # measured latitudes alone are 153.8 m apart: beyond the 150 m containment radius
    assert distance_m(osm, gancio) > SAME_PLACE_METERS
    merged, _ = merge([[osm], [gancio]])
    assert [v.id for v in merged] == ["osm:node/523776298", "gancio:villemorte:1"]
    # 100 m apart they would merge now that ’ separates words (before: "lepiceriemoderne")
    gancio.latitude = 45.6747674 - 0.0009
    merged, alias = merge([[osm], [gancio]])
    assert [v.id for v in merged] == ["osm:node/523776298"]
    assert alias["gancio:villemorte:1"] == "osm:node/523776298"


def test_default_category_is_not_stricter_than_the_place_fallback(tz):
    entry = {"name": "X", "venue": "Salle Imaginaire Nord", "reader": {}}
    (venue,) = configured_venues((entry,), [])
    assert venue.category == "events_venue"
    for title in ("Lemon Twigs", "Concert Lemon Twigs", "Atelier collage"):
        ev = _epicerie_event(title, tz, location="Salle Imaginaire Nord")
        now = _now(tz)
        fallback = build_concerts([ev], {}, now=now, window_days=60, tz=tz)
        configured = build_concerts([ev], {venue.id: venue}, now=now, window_days=60, tz=tz)
        assert [c.reason for c in configured] == [c.reason for c in fallback], title


def test_curly_apostrophes_separate_words():
    straight = place_tokens("L'Épicerie Moderne")
    assert straight == ("epicerie", "moderne")
    for curly in ("L’Épicerie Moderne", "L‘Épicerie Moderne", "Lʼ Épicerie Moderne"):
        assert place_tokens(curly) == straight, curly


def test_shorter_known_names_are_decoys_not_matches(tz):
    building = Venue("osm:way/1", "Opéra de Lyon", 45.7676, 4.8361, "theatre")
    bar = Venue("osm:node/5", "Underground", 45.75, 4.84, "bar")
    restaurant = Venue("osm:node/6", "Le Marché", 45.74, 4.83, "restaurant")
    marche = next(e for e in ZONE.priority_venues if e["name"] == "Le Marché Gare")
    added = configured_venues((OPERA, marche), [building, bar, restaurant])
    assert [v.id for v in added] == ["config:operaunderground", "config:marchegare"]
    assert (bar.category, restaurant.category) == ("bar", "restaurant")
    known = [building, bar, restaurant, *added]
    ids, _ = configured_venue_ids((OPERA, marche), known)
    assert ids == {
        "Opéra Underground": "config:operaunderground",
        "Le Marché Gare": "config:marchegare",
    }
    ev = _epicerie_event("Quatuor Béla", tz, location="Opéra Underground")
    attach_to_configured([ev], ids)
    venues = {v.id: v for v in known}
    (concert,) = build_concerts([ev], venues, now=_now(tz), window_days=60, tz=tz)
    assert (concert.venue_id, concert.reason) == ("config:operaunderground", "music venue")
