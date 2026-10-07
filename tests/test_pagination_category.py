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
from nightcrawler.events import build_concerts
from nightcrawler.http import Fetcher
from nightcrawler.models import RawEvent, Venue
from nightcrawler.sources import listing_jsonld
from nightcrawler.venues import configured_venues

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
    respx.get("https://v.example/agenda/202612").respond(404)
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
    assert pages == 4


def test_configured_venue_is_created_when_no_known_venue_matches(tz):
    building = Venue("osm:way/1", "Opéra de Lyon", 45.7676, 4.8361, "theatre")
    added = configured_venues((OPERA,), [building])
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
    concerts = build_concerts([ev, tm], venues, now=_now(tz), window_days=60, tz=tz)
    assert [(c.venue_id, c.reason, c.sources) for c in concerts] == [
        ("config:operaunderground", "music venue", ["listing_jsonld:www.opera-lyon.com"]),
        ("osm:way/1", "ticketing category: music", ["ticketmaster"]),  # no coordinates: apart
    ]


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
        ("", ", paginate: {param: page, start: -1, max: 2}", "paginate"),
        ("", ", paginate: {start: 1, max: 2}", "paginate"),
        ("", ", paginate: {param: page, start: 1, max: 0}", "paginate"),
    ],
)
def test_zone_config_checks_category_coordinates_and_paginate(tmp_path, venue, reader, error):
    lines = [f"    {f}\n" for f in venue.strip("{}").split(", ") if f]
    path = tmp_path / "zone.yaml"
    path.write_text(
        "name: T\nlatitude: 45\nlongitude: 4\nradius_km: 1\npriority_venues:\n  - name: V\n"
        + "".join(lines)
        + f"    reader: {READER}{reader}}}\n"
    )
    with pytest.raises(ValueError, match=error):
        load_zone(path)


def test_zone_config_opera_and_pagination_entries():
    readers = {e["name"]: e["reader"] for e in ZONE.priority_venues}
    assert OPERA["category"] == "music_venue" and "latitude" not in OPERA
    assert readers["Opéra Underground"]["paginate"] == {"param": "page", "start": 2, "max": 5}
    # the Épicerie and Marché Gare "Afficher plus" links are ?page=1: pages count from 0
    for name in ("L'Épicerie Moderne", "Le Marché Gare"):
        assert readers[name]["paginate"]["start"] == 1 and len(readers[name]["urls"]) == 1
