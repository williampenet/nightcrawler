"""Listing -> detail JSON-LD reader (WIP-62).

Fixtures in `fixtures/listing_jsonld/` are reconstructed from the 2026-10-07 capture of the
four venues (see the README there). The readers under test are the real entries of
`config/zone.yaml`, so their URLs and link regexes are checked against those structures.
"""

from datetime import datetime
from pathlib import Path

import pytest
import respx

from nightcrawler.config import load_zone
from nightcrawler.events import build_concerts
from nightcrawler.http import Fetcher
from nightcrawler.models import RawEvent, Venue
from nightcrawler.sources import listing_jsonld, priority

ZONE = load_zone(Path(__file__).parents[1] / "config/zone.yaml")
ENTRIES = {e["name"]: e for e in ZONE.priority_venues}
EPICERIE = ENTRIES["L'Épicerie Moderne"]
MARCHE = ENTRIES["Le Marché Gare"]
AUDITORIUM = ENTRIES["Auditorium de Lyon"]
OPERA = ENTRIES["Opéra Underground"]
EMPTY = "<html><body>no JSON-LD here</body></html>"


@pytest.fixture
def page(fixture_text):
    return lambda name: fixture_text(f"listing_jsonld/{name}")


def _now(tz, day=7, month=10):
    return datetime(2026, month, day, 9, tzinfo=tz)


def _read(entry, tz, now=None, days=60, reader=None):
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    reader = reader or entry["reader"]
    return listing_jsonld.read(reader, entry["venue"], fetcher, now or _now(tz), tz, days)


def _robots(*hosts):
    for host in hosts:
        respx.get(f"https://{host}/robots.txt").respond(404)


@respx.mock
def test_top_level_object_links_filtered_and_venue_configured(page, tz):
    _robots("epiceriemoderne.com")
    base = "https://epiceriemoderne.com/agenda"
    respx.get(base, params={"page": "1"}).respond(404)
    respx.get(base).respond(200, html=page("epicerie_agenda.html"))
    respx.get(f"{base}/2026-10-08-the-lemon-twigs").respond(
        200, html=page("epicerie_lemon_twigs.html")
    )
    respx.get(f"{base}/2026-10-19-temples").respond(200, html=page("epicerie_temples.html"))
    # no route for the scolaire, off-host or javascript links: respx fails if they are fetched
    events, pages, status = _read(EPICERIE, tz)
    assert [(e.title, e.start.isoformat()) for e in events] == [
        ("THE LEMON TWIGS", "2026-10-08T19:30:00+02:00"),
        ("TEMPLES", "2026-10-19T20:00:00+02:00"),
    ]
    ev = events[0]
    assert ev.source == ev.venue_id == "listing_jsonld:epiceriemoderne.com"
    assert ev.location_name == "L'Épicerie Moderne"
    assert ev.url == f"{base}/2026-10-08-the-lemon-twigs"
    assert (pages, status) == (3, "ok")  # ?page=1 answers 404: past the last page, no error


@respx.mock
def test_events_outside_the_window_are_dropped(page, tz):
    _robots("epiceriemoderne.com")
    respx.get("https://epiceriemoderne.com/agenda").respond(200, html=page("epicerie_agenda.html"))
    respx.get(url__regex=r".*/2026-10-08-the-lemon-twigs$").respond(
        200, html=page("epicerie_lemon_twigs.html")
    )
    respx.get(url__regex=r".*/2026-10-19-temples$").respond(200, html=page("epicerie_temples.html"))
    events, _, _ = _read(EPICERIE, tz, now=_now(tz, day=10), days=5)
    assert events == []  # 8 Oct is past, 19 Oct beyond 15 Oct


@respx.mock
def test_detail_errors_and_pages_without_event_are_counted(page, tz):
    _robots("marchegare.fr")
    respx.get("https://marchegare.fr/agenda").respond(200, html=page("marchegare_agenda.html"))
    base = "https://marchegare.fr/agenda"
    respx.get(f"{base}/lucio-bukowski-anton-serra-oster-lapwass-nouvelle-date").respond(
        200, html=page("marchegare_lucio.html")
    )
    respx.get(f"{base}/hypno5e-hippotraktor").respond(500)
    respx.get(f"{base}/optimiser-sa-residence-artistique").respond(200, html=EMPTY)
    events, pages, status = _read(MARCHE, tz)
    assert [e.title for e in events] == [
        "Lucio Bukowski + Anton Serra + OSter Lapwass (nouvelle date)"
    ]
    assert events[0].location_name == "Le Marché Gare"
    assert (pages, status) == (4, "detail errors: 1")  # ?page=1 served page 0 again: stop


def test_month_template_expansion(tz):
    reader = {"urls": ["https://a.example/agenda/{yyyymm}", "https://a.example/fixed"]}
    assert listing_jsonld.listing_urls(reader, _now(tz, day=20, month=12), 60) == [
        "https://a.example/agenda/202612",
        "https://a.example/agenda/202701",
        "https://a.example/agenda/202702",  # 20 Dec + 60 days = 18 Feb
        "https://a.example/fixed",
    ]


@respx.mock
def test_graph_naive_date_and_category_filter(page, tz):
    _robots("www.auditorium-lyon.com")
    base = "https://www.auditorium-lyon.com/fr"
    for month in ("202610", "202612"):  # 7 Oct + 60 days = 6 Dec: three monthly listings
        respx.get(f"{base}/agenda/{month}").respond(200, html=EMPTY)
    respx.get(f"{base}/agenda/202611").respond(200, html=page("auditorium_202611.html"))
    respx.get(f"{base}/saison-2026-27/musiques-monde/orchestre-arabo-andalou-du-maroc").respond(
        200, html=page("auditorium_arabo_andalou.html")
    )
    family = respx.get(f"{base}/saison-2026-27/famille/concert-famille-novembre").respond(
        200, html=EMPTY
    )
    # scolaires and atelier-* pages have no route: fetching them would fail the test
    events, pages, status = _read(AUDITORIUM, tz)
    (ev,) = events
    assert ev.title == "Orchestre arabo-andalou du Maroc"
    assert ev.start.isoformat() == "2026-11-02T20:00:00+01:00"  # no offset: Europe/Paris
    assert ev.location_name == "Auditorium Maurice-Ravel"  # not the JSON-LD location
    assert family.called  # family concerts are kept
    assert (pages, status) == (5, "ok")


@respx.mock
def test_array_of_events_with_http_type(page, tz):
    _robots("www.opera-lyon.com")
    respx.get(url__startswith="https://www.opera-lyon.com/programmation-reservations/").respond(
        200, html=page("opera_listing.html")
    )
    base = "https://www.opera-lyon.com/fr/programmation/saison-2026-2027/opera-underground"
    respx.get(f"{base}/leila-martial-elie-dufour-karma-bazar").respond(
        200, html=page("opera_karma_bazar.html")
    )
    respx.get(f"{base}/quatuor-bela").respond(200, html=EMPTY)
    events, _, status = _read(OPERA, tz)
    assert sorted(e.start.day for e in events) == [9, 10]  # one Event per performance
    assert {e.title for e in events} == {'Leïla Martial & Elie Dufour "Karma Bazar"'}
    assert {e.location_name for e in events} == {"Opéra Underground"}  # not "Opéra de Lyon"
    assert status == "ok"


def test_event_links_same_host_include_exclude(page):
    links = listing_jsonld.event_links(
        page("epicerie_agenda.html"),
        "https://epiceriemoderne.com/agenda",
        r"^/agenda/20\d\d-",
        "scolaire",
    )
    assert links == [  # fragment duplicate merged, off-host and javascript: dropped
        "https://epiceriemoderne.com/agenda/2026-10-08-the-lemon-twigs",
        "https://epiceriemoderne.com/agenda/2026-10-19-temples",
    ]
    no_exclude = listing_jsonld.event_links(
        page("epicerie_agenda.html"), "https://epiceriemoderne.com/agenda", r"^/agenda/20", None
    )
    assert len(no_exclude) == 3


@respx.mock
def test_detail_cap(page, tz):
    _robots("epiceriemoderne.com")
    respx.get("https://epiceriemoderne.com/agenda").respond(200, html=page("epicerie_agenda.html"))
    first = respx.get(url__regex=r".*/2026-10-08-the-lemon-twigs$").respond(
        200, html=page("epicerie_lemon_twigs.html")
    )
    reader = EPICERIE["reader"] | {"urls": ["https://epiceriemoderne.com/agenda"]}
    events, pages, status = _read(EPICERIE, tz, reader=reader | {"max_details": 1})
    assert first.call_count == 1 and len(events) == 1  # the second link is never fetched
    assert (pages, status) == (3, "detail_cap: 1 of 2 links")  # /agenda, ?page=1, 1 detail


@respx.mock
def test_markup_and_links_are_untrusted(tz):
    _robots("v.example")
    respx.get("https://v.example/agenda").respond(
        200, html='<a href="/e/1">x</a><a href="http://v.example/e/2">downgrade</a>'
    )
    jsonld = (
        '{"@type": "MusicEvent", "name": "<b>Nuit</b> <em>&amp;</em> Drone",'
        ' "startDate": "2026-10-20T21:00:00+02:00", "url": "javascript:alert(1)",'
        ' "offers": {"url": "data:text/html,x"}}'
    )
    respx.get("https://v.example/e/1").respond(
        200, html=f'<script type="application/ld+json">{jsonld}</script>'
    )
    entry = {"venue": "V", "reader": {"urls": ["https://v.example/agenda"], "include": "^/e/"}}
    (ev,) = _read(entry, tz)[0]
    assert ev.title == "Nuit & Drone"
    assert ev.url == "https://v.example/e/1" and ev.ticket_url is None


@respx.mock
def test_errors_go_to_the_status_row(page, tz):
    respx.get("https://marchegare.fr/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    _robots("epiceriemoderne.com", "www.opera-lyon.com")
    respx.get(host="epiceriemoderne.com").respond(500)
    respx.get(url__regex=r".*karma-bazar$").respond(200, html=page("opera_karma_bazar.html"))
    respx.get(url__regex=r".*quatuor-bela$").respond(200, html=EMPTY)
    respx.get(host="www.opera-lyon.com").respond(200, html=page("opera_listing.html"))
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    events, rows = priority.collect((MARCHE, EPICERIE, OPERA), fetcher, _now(tz), tz, 60)
    assert [(r["name"], r["reader"], r["status"], r["events"]) for r in rows] == [
        ("Le Marché Gare", "listing_jsonld", "robots_blocked", 0),
        ("L'Épicerie Moderne", "listing_jsonld", "error: no listing page read (HTTP 500)", 0),
        ("Opéra Underground", "listing_jsonld", "ok", 2),
    ]
    assert set(rows[2]) == {"name", "venue", "reader", "status", "events", "pages", "last"}
    # the farthest date read (WIP-107); None when the venue gave nothing
    assert rows[2]["last"] == max(e.start.date() for e in events).isoformat()
    assert rows[0]["last"] is None and rows[1]["last"] is None
    assert len(events) == 2


@respx.mock
def test_events_attach_to_configured_venue_and_dedupe(page, tz):
    _robots("www.opera-lyon.com")
    respx.get(url__startswith="https://www.opera-lyon.com/programmation-reservations/").respond(
        200, html=page("opera_listing.html")
    )
    respx.get(url__regex=r".*karma-bazar$").respond(200, html=page("opera_karma_bazar.html"))
    respx.get(url__regex=r".*quatuor-bela$").respond(200, html=EMPTY)
    events, _, _ = _read(OPERA, tz)
    building = Venue("osm:way/1", "Opéra de Lyon", 45.7676, 4.8361, "theatre")
    underground = Venue("osm:node/2", "Opéra Underground", 45.7676, 4.8361, "music_venue")
    oct10 = next(e for e in events if e.start.day == 10)
    tm = RawEvent(
        'LEÏLA MARTIAL & ELIE DUFOUR "KARMA BAZAR"', oct10.start, "ticketmaster", "osm:node/2"
    )
    venues = {v.id: v for v in (building, underground)}
    concerts = build_concerts([*events, tm], venues, now=_now(tz), window_days=60, tz=tz)
    assert [(c.start[:10], c.venue_id, c.sources) for c in concerts] == [
        ("2026-10-09", "osm:node/2", ["listing_jsonld:www.opera-lyon.com"]),
        ("2026-10-10", "osm:node/2", ["listing_jsonld:www.opera-lyon.com", "ticketmaster"]),
    ]


@pytest.mark.parametrize(
    "reader, error",
    [
        ("{type: listing_jsonld, include: x}", "urls must be"),
        ("{type: listing_jsonld, urls: ['http://v.example/'], include: x}", "https"),
        ("{type: listing_jsonld, urls: ['https://v.example/{yyyy}'], include: x}", "yyyymm"),
        ("{type: listing_jsonld, urls: ['https://v.example/']}", "include must be a regex"),
        ("{type: listing_jsonld, urls: ['https://v.example/'], include: '('}", "include"),
        ("{type: listing_jsonld, urls: ['https://v.example/'], include: x, exclude: '['}", "excl"),
        ("{type: listing_jsonld, urls: ['https://v.example/'], include: x, max_details: 0}", "int"),
        ("{type: rss, url: 'https://v.example/'}", "unknown reader type"),
    ],
)
def test_zone_config_checks_listing_reader(tmp_path, reader, error):
    path = tmp_path / "zone.yaml"
    path.write_text(
        "name: T\nlatitude: 45\nlongitude: 4\nradius_km: 1\npriority_venues:\n"
        f"  - name: V\n    reader: {reader}\n"
    )
    with pytest.raises(ValueError, match=error):
        load_zone(path)


def test_zone_config_has_the_four_listing_readers():
    assert {n for n, e in ENTRIES.items() if e["reader"]["type"] == "listing_jsonld"} == {
        "Opéra Underground",
        "L'Épicerie Moderne",
        "Le Marché Gare",
        "Auditorium de Lyon",
    }
    listing = [e for e in ENTRIES.values() if e["reader"]["type"] == "listing_jsonld"]
    assert all(e["reader"].get("max_details") for e in listing)


def _jsonld(name="Live", start="2026-10-20T21:00:00+02:00"):
    data = f'{{"@type": "MusicEvent", "name": "{name}", "startDate": "{start}"}}'
    return f'<script type="application/ld+json">{data}</script>'


def _entry(**reader):
    base = {"urls": ["https://v.example/agenda"], "include": "^/e/"}
    return {"venue": "V", "reader": base | reader}


@respx.mock
def test_origin_is_the_configured_url_not_the_redirect(tz):
    _robots("v.example", "w.example")
    respx.get("https://v.example/agenda").respond(301, headers={"Location": "https://w.example/a"})
    respx.get("https://w.example/a").respond(
        200, html='<a href="/e/moved">redirect host</a><a href="https://v.example/e/1">x</a>'
    )
    respx.get("https://v.example/e/1").respond(200, html=_jsonld())
    events, _, status = _read(_entry(), tz)  # no route for w.example/e/moved
    assert [e.url for e in events] == ["https://v.example/e/1"] and status == "ok"


@respx.mock
def test_empty_results_have_their_own_status(tz):
    _robots("v.example")
    respx.get("https://v.example/agenda").respond(200, html='<a href="/e/1">x</a>')
    respx.get("https://v.example/e/1").respond(200, html=EMPTY)
    assert _read(_entry(), tz)[2] == "no_events"
    assert _read(_entry(include="^/nothing/"), tz)[1:] == (1, "no_links")


@respx.mock
def test_one_bad_detail_page_is_a_detail_error(tz):
    _robots("v.example")
    respx.get("https://v.example/agenda").respond(200, html='<a href="/e/1">x</a><a href="/e/2">')
    nested = "[" * 100_000 + "]" * 100_000  # past the recursion limit of json / the tree walk
    respx.get("https://v.example/e/1").respond(
        200, html=f'<script type="application/ld+json">{nested}</script>'
    )
    respx.get("https://v.example/e/2").respond(200, html=_jsonld("Kept"))
    events, pages, status = _read(_entry(), tz)
    assert [e.title for e in events] == ["Kept"]
    assert (pages, status) == (2, "detail errors: 1")
