"""WordPress REST reader (WIP-60).

Fixture `wp_json_transbordeur.json`: 8 real items of
https://www.transbordeur.fr/wp-json/wp/v2/evenement (pages 1, 2 and 20 at per_page=5,
read on 2026-10-06), trimmed to the public event fields the reader uses.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx

from nightcrawler.config import load_zone
from nightcrawler.events import build_concerts
from nightcrawler.http import Fetcher
from nightcrawler.models import RawEvent, Venue
from nightcrawler.sources import priority, wp_json

URL = "https://venue.example/wp-json/wp/v2/evenement"
READER = {
    "type": "wp_json",
    "url": URL,
    "fields": {
        "title": "title.rendered",
        "date": "acf.date",
        "time": "acf.hour",
        "ticket": "acf.bouton_booking.url",
        "link": "link",
    },
    "date_format": "%Y%m%d",
    "time_format": "%H:%M:%S",
}
ENTRY = {"name": "Venue", "venue": "Le Transbordeur", "reader": READER}


@pytest.fixture
def items(fixture_text):
    return json.loads(fixture_text("wp_json_transbordeur.json"))


def _now(tz):
    return datetime(2026, 10, 6, 12, tzinfo=tz)


def _item(title="Live", date="20261110", hour="20:00:00", link="https://venue.example/e/1"):
    return {"title": {"rendered": title}, "acf": {"date": date, "hour": hour}, "link": link}


def test_parse_real_items_in_window(items, tz):
    events = wp_json.parse(items, READER, "Le Transbordeur", _now(tz), tz, 60)
    assert [e.title for e in events] == [
        "AL-WALID + K3NYA + SLMVX + VVS237",
        "THE STRANGLERS + BROTHER JUNIOR",
        "BEAST IN BLACK + FROZEN CROWN + SONATA ARCTICA",
    ]  # the five 2027 dates are past the 60-day window
    ev = events[1]
    assert ev.source == "wp_json:venue.example" and ev.location_name == "Le Transbordeur"
    assert ev.url == "https://www.transbordeur.fr/evenement/the-stranglers-brother-junior-30112026/"
    assert ev.ticket_url.startswith("https://web.digitick.com/the-stranglers-concert-")


def test_dates_are_local_time(items, tz):
    events = wp_json.parse(items, READER, "X", _now(tz), tz, 365)
    by_title = {e.title: e for e in events}
    al = by_title["AL-WALID + K3NYA + SLMVX + VVS237"]  # 20261110 23:30:00, winter time
    assert al.start.isoformat() == "2026-11-10T23:30:00+01:00"
    assert al.start.astimezone(UTC).hour == 22
    summer = by_title["GROUNDATION"]  # 20270520 18:30:00, summer time
    assert summer.start.isoformat() == "2027-05-20T18:30:00+02:00"


def test_missing_or_bad_hour_keeps_the_day(tz):
    events = wp_json.parse(
        [_item(title="A", hour=None), _item(title="B", hour="soon")], READER, "X", _now(tz), tz, 60
    )
    assert [(e.title, e.start.hour, e.start.minute) for e in events] == [("A", 0, 0), ("B", 0, 0)]


def test_html_is_stripped_and_links_checked(tz):
    item = _item(title="<em>Nuit</em> &#8211; <b>Drone</b> &amp; Noise", link="javascript:alert(1)")
    item["acf"]["bouton_booking"] = {"url": "data:text/html,x"}
    (ev,) = wp_json.parse([item], READER, "X", _now(tz), tz, 60)
    assert ev.title == "Nuit – Drone & Noise"
    assert ev.url is None and ev.ticket_url is None


def test_malformed_items_are_skipped(tz):
    bad = [
        "not an object",
        None,
        {"acf": {"date": "20261110"}},  # no title
        _item(date="10/11/2026"),  # wrong format
        _item(date=None),
        _item(date=True),
        {"title": "flat string", "acf": "not an object"},
    ]
    events = wp_json.parse([*bad, _item(title="Kept")], READER, "X", _now(tz), tz, 60)
    assert [e.title for e in events] == ["Kept"]
    assert wp_json.parse({"code": "rest_no_route"}, READER, "X", _now(tz), tz, 60) == []


def _fetcher():
    return Fetcher(cache_dir=None, min_interval=0)


def _pages(items, per_page):
    return [items[i : i + per_page] for i in range(0, len(items), per_page)]


@respx.mock
def test_pagination_stops_on_short_page(items, tz):
    respx.get("https://venue.example/robots.txt").respond(200, text="User-agent: *\nDisallow:\n")
    route = respx.get(URL).mock(
        side_effect=[respx.MockResponse(200, json=p) for p in _pages(items, 3)]
    )
    entry = ENTRY | {"reader": READER | {"per_page": 3}}
    events, rows = priority.collect((entry,), _fetcher(), _now(tz), tz, 60)
    assert route.call_count == 3  # 3 + 3 + 2 items
    assert [dict(c.request.url.params) for c in route.calls][-1] == {"per_page": "3", "page": "3"}
    assert len(events) == 3
    assert rows == [
        {"name": "Venue", "venue": "Le Transbordeur", "reader": "wp_json"}
        | {"status": "ok", "events": 3, "pages": 3}
    ]


@respx.mock
def test_pagination_stops_past_last_page(items, tz):
    respx.get("https://venue.example/robots.txt").respond(404)
    respx.get(URL).mock(  # two full pages, then what WordPress answers past the end
        side_effect=[respx.MockResponse(200, json=p) for p in _pages(items, 4)]
        + [respx.MockResponse(400, json={"code": "rest_post_invalid_page_number"})]
    )
    entry = ENTRY | {"reader": READER | {"per_page": 4}}
    _, rows = priority.collect((entry,), _fetcher(), _now(tz), tz, 60)
    assert (rows[0]["status"], rows[0]["pages"], rows[0]["events"]) == ("ok", 2, 3)


@respx.mock
def test_pagination_stops_at_page_cap(items, tz):
    respx.get("https://venue.example/robots.txt").respond(404)
    route = respx.get(URL).mock(
        side_effect=[respx.MockResponse(200, json=p) for p in _pages(items, 4)]
    )
    entry = ENTRY | {"reader": READER | {"per_page": 4, "max_pages": 1}}
    _, rows = priority.collect((entry,), _fetcher(), _now(tz), tz, 60)
    assert route.call_count == 1  # a second full page exists, but the cap is 1
    assert (rows[0]["status"], rows[0]["pages"]) == ("page_cap", 1)


@respx.mock
def test_robots_and_errors_never_stop_the_run(tz):
    respx.get("https://venue.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    route = respx.get(URL).respond(200, json=[])
    respx.get("https://other.example/robots.txt").respond(404)
    respx.get("https://other.example/wp-json/wp/v2/e").respond(500)
    respx.get("https://broken.example/robots.txt").respond(404)
    respx.get("https://broken.example/wp-json/wp/v2/e").respond(200, text='[{"title": ')
    other = ENTRY | {"reader": READER | {"url": "https://other.example/wp-json/wp/v2/e"}}
    broken = ENTRY | {"reader": READER | {"url": "https://broken.example/wp-json/wp/v2/e"}}
    events, rows = priority.collect((ENTRY, other, broken), _fetcher(), _now(tz), tz, 60)
    assert events == [] and not route.called
    assert [r["status"] for r in rows] == ["robots_blocked", "error: HTTP 500", rows[2]["status"]]
    assert rows[2]["status"].startswith("error: ")  # truncated JSON


def test_events_attach_to_the_known_venue_and_dedupe(tz):
    venue = Venue("osm:node/1", "Transbordeur", 45.7839, 4.8608, "music_venue")
    (ev,) = wp_json.parse([_item(title="Nuit Drone")], READER, "Le Transbordeur", _now(tz), tz, 60)
    tm = RawEvent("NUIT DRONE", ev.start, "ticketmaster", "osm:node/1", url="https://tm.example/1")
    concerts = build_concerts([ev, tm], {venue.id: venue}, now=_now(tz), window_days=60, tz=tz)
    assert [(c.venue_id, c.sources) for c in concerts] == [
        ("osm:node/1", ["wp_json:venue.example", "ticketmaster"])
    ]


def test_zone_config_has_transbordeur_reader():
    zone = load_zone(Path(__file__).parents[1] / "config/zone.yaml")
    entry = next(e for e in zone.priority_venues if e["name"] == "Le Transbordeur")
    assert entry["reader"]["type"] == "wp_json"
    assert entry["reader"]["fields"]["date"] == "acf.date"


def test_zone_config_rejects_incomplete_reader(tmp_path):
    path = tmp_path / "zone.yaml"
    path.write_text(
        "name: T\nlatitude: 45\nlongitude: 4\nradius_km: 1\npriority_venues:\n"
        "  - name: V\n    reader: {type: wp_json, url: 'https://v.example/', fields: {title: t}}\n"
    )
    with pytest.raises(ValueError, match="fields.date"):
        load_zone(path)


@pytest.mark.parametrize(
    "extra, ok",
    [
        ("per_page: 100, max_pages: 1", True),
        ("per_page: 101", False),  # WordPress caps per_page at 100
        ("per_page: 0", False),
        ("per_page: '50'", False),
        ("per_page: 2.5", False),
        ("max_pages: -1", False),
        ("max_pages: true", False),
    ],
)
def test_zone_config_checks_page_numbers(tmp_path, extra, ok):
    path = tmp_path / "zone.yaml"
    path.write_text(
        "name: T\nlatitude: 45\nlongitude: 4\nradius_km: 1\npriority_venues:\n"
        "  - name: V\n    reader: {type: wp_json, url: 'https://v.example/', "
        f"fields: {{title: t, date: d}}, date_format: '%Y', {extra}}}\n"
    )
    if ok:
        assert load_zone(path).priority_venues[0]["reader"]["per_page"] == 100
    else:
        with pytest.raises(ValueError, match="must be an int"):
            load_zone(path)


@respx.mock
def test_truncated_page_keeps_earlier_pages(items, tz, monkeypatch):
    respx.get("https://venue.example/robots.txt").respond(404)
    first, second = _pages(items, 4)
    respx.get(URL).mock(
        side_effect=[respx.MockResponse(200, json=first), respx.MockResponse(200, json=second)]
    )
    # the second page is larger than the cap: the fetcher would have cut it mid-JSON
    monkeypatch.setattr(wp_json, "MAX_BYTES", len(json.dumps(first)) + 10)
    entry = ENTRY | {"reader": READER | {"per_page": 4}}
    events, rows = priority.collect((entry,), _fetcher(), _now(tz), tz, 365)
    assert (rows[0]["status"], rows[0]["pages"]) == ("truncated", 1)
    assert {e.title for e in events} == {"ORIA", "ALOISE SAUVAGE", "SAM QUEALY", "GROUNDATION"}
