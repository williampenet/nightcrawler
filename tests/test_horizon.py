"""WIP-107: every published date up to the horizon (config/zone.yaml `window_days`, at most
events.MAX_HORIZON_DAYS = the extract.py date bound), caps that show when they bind, and the
completeness report (per source beyond 60 days, farthest date, cap hits, "Mes salles")."""

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from nightcrawler import completeness
from nightcrawler.cli import one_line
from nightcrawler.config import load_zone
from nightcrawler.events import MAX_HORIZON_DAYS, build_concerts, in_window
from nightcrawler.extract import grounded
from nightcrawler.http import Fetcher
from nightcrawler.models import Concert, RawEvent, Venue
from nightcrawler.pipeline import summary_markdown
from nightcrawler.sources import gancio, listing_jsonld, ticketmaster

ROOT = Path(__file__).parents[1]
ZONE = load_zone(ROOT / "config/zone.yaml")


def _now(tz):
    return datetime(2026, 10, 9, 22, 0, tzinfo=tz)


def _ev(tz, day: date, title="Concert", source="json-ld"):
    start = datetime(day.year, day.month, day.day, 20, 0, tzinfo=tz)
    return RawEvent(title=title, start=start, source=source, venue_id="v", types=["MusicEvent"])


# -- horizon --------------------------------------------------------------------------------


def test_zone_collects_up_to_the_extract_bound():
    assert ZONE.window_days == MAX_HORIZON_DAYS == 400


@pytest.mark.parametrize("value", [0, 401, "400", True, 60.5])
def test_window_days_is_checked(tmp_path, value):
    path = tmp_path / "zone.yaml"
    head = "name: X\nlatitude: 45.7\nlongitude: 4.8\nradius_km: 5\n"
    path.write_text(f"{head}window_days: {json.dumps(value)}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="window_days"):
        load_zone(path)


def test_horizon_bound_is_the_extract_bound(tz):
    """A date 400 days ahead is kept by the window and by the model-read check; 401 is not."""
    now = _now(tz)
    today = now.date()
    last = date.fromordinal(today.toordinal() + MAX_HORIZON_DAYS)
    after = date.fromordinal(last.toordinal() + 1)
    # the run ends at now + 400 days (22:00): a 20:00 concert that day is in, the next day out
    assert in_window(_ev(tz, last), now, ZONE.window_days)
    assert not in_window(_ev(tz, after), now, ZONE.window_days)
    text = f"{last.day} {['', 'janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'][last.month]}\nDuo Esperanza"  # noqa: E501
    ev = {"title": "Duo Esperanza", "date": last.isoformat(), "time": None, "performers": [],
          "is_concert": True}  # fmt: skip
    assert grounded(ev, text, today)[0] is not None
    assert grounded(ev | {"date": after.isoformat()}, text, today)[1] == "date out of range"


def test_far_concerts_are_kept(tz):
    now = _now(tz)
    venue = Venue("v", "Salle", 45.75, 4.85, "music_venue")
    days = [date(2026, 10, 10), date(2026, 12, 20), date(2027, 9, 30), date(2027, 11, 20)]
    raw = [_ev(tz, d, title=f"Concert {n}") for n, d in enumerate(days)]
    kept = build_concerts(raw, {"v": venue}, now=now, window_days=ZONE.window_days, tz=tz)
    assert [c.start[:10] for c in kept] == ["2026-10-10", "2026-12-20", "2027-09-30"]


def test_month_listings_span_the_horizon(tz):
    """The Auditorium reads one listing per month: 14 months for 400 days from 9 Oct 2026."""
    reader = {"urls": ["https://a.example/agenda/{yyyymm}"]}
    urls = listing_jsonld.listing_urls(reader, _now(tz), ZONE.window_days)
    assert urls[0].endswith("202610") and urls[-1].endswith("202711") and len(urls) == 14


def test_ticketmaster_and_gancio_ask_for_the_horizon(tz):
    p = ticketmaster.params_for(ZONE, "k", _now(tz), 0)
    assert p["endDateTime"] == "2027-11-13T21:00:00Z"  # now + 400 days, in UTC
    g = gancio.window_params(ZONE, _now(tz))
    assert int(g["end"]) == int(datetime(2027, 11, 14, tzinfo=tz).timestamp())


# -- caps -----------------------------------------------------------------------------------


def test_listing_caps_are_sized_by_the_horizon():
    """A capped listing reader loses concerts (the detail page holds the event), so every
    cap is at least one per day of the horizon and pagination runs until no new link."""
    readers = [e["reader"] for e in ZONE.priority_venues if e["reader"]["type"] == "listing_jsonld"]
    assert len(readers) == 4
    for r in readers:
        assert r["max_details"] >= ZONE.window_days
        assert "paginate" not in r or r["paginate"]["max"] >= 20


@respx.mock
def test_unpublished_month_is_not_an_error(tz):
    respx.get("https://v.example/robots.txt").respond(404)
    respx.get("https://v.example/agenda/202610").respond(200, html='<a href="/e/1">x</a>')
    respx.get(url__regex=r"https://v\.example/agenda/2027\d\d$").respond(404)
    respx.get(url__regex=r"https://v\.example/agenda/20261[12]$").respond(404)
    respx.get("https://v.example/e/1").respond(200, html="<html></html>")
    reader = {"urls": ["https://v.example/agenda/{yyyymm}"], "include": "^/e/"}
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    _, _, status = listing_jsonld.read(reader, "V", fetcher, _now(tz), tz, ZONE.window_days)
    assert status == "no_events; unpublished months: 13"
    # a configured URL (no {yyyymm}) answering 404 is still a failure
    respx.get("https://v.example/fixed").respond(404)
    with pytest.raises(ValueError, match="HTTP 404"):
        listing_jsonld.read(
            {"urls": ["https://v.example/fixed"], "include": "^/e/"}, "V", fetcher, _now(tz), tz, 60
        )


@respx.mock
def test_gancio_detail_cap_is_in_the_status(zone, tz, fixture_text, monkeypatch):
    monkeypatch.setattr(gancio, "MAX_DETAILS", 2)
    base = "https://agenda.example"
    respx.get(base + "/robots.txt").respond(404)
    respx.get(base + "/api/events").respond(200, text=fixture_text("gancio_events.json"))
    detail = respx.get(url__startswith=base + "/api/event/detail/").respond(200, json={})
    z = replace(zone, gancio_instances=({"name": "Test", "url": base},))
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    _, events, status = gancio.collect(z, fetcher, _now(tz).replace(month=10, day=5), tz)
    wanted = len(gancio.detail_order(events, cap=len(events)))
    assert wanted > 2 and detail.call_count == 2
    assert status == f"ok; Test: detail_cap: 2 of {wanted}"
    assert completeness.cap_notes(status) == ["detail_cap"]
    # details=False reads none; fetch_details reads them later with the same cap and note
    _, events, status = gancio.collect(z, fetcher, _now(tz).replace(month=10, day=5), tz, False)
    assert status == "ok" and detail.call_count == 2
    assert gancio.fetch_details(z, fetcher, events) == [f"Test: detail_cap: 2 of {wanted}"]
    assert detail.call_count == 4


@respx.mock
def test_ticketmaster_page_cap_is_in_the_status(zone, tz, fixture_text, monkeypatch):
    monkeypatch.setenv("TICKETMASTER_API_KEY", "k")
    monkeypatch.setattr(ticketmaster, "MAX_PAGES", 2)
    payload = json.loads(fixture_text("ticketmaster.json")) | {"page": {"totalPages": 7}}
    route = respx.get(ticketmaster.API_URL).mock(return_value=httpx.Response(200, json=payload))
    fetcher = Fetcher(cache_dir=None, min_interval=0)
    _, _, status = ticketmaster.collect(zone, fetcher, _now(tz), tz)
    assert route.call_count == 2 and status == "ok; page_cap: 2 of 7 pages"
    assert completeness.cap_notes(status) == ["page_cap"]


# -- report ---------------------------------------------------------------------------------


def _concert(start, sources):
    return Concert("id", "Secret Title", start, "v", "Salle", None, None, ["Act"], sources, "")


def test_cap_notes_by_kind():
    status = (
        "ok; chunks 5, model 1; page_cap: https://x/agenda; detail_cap: 40; detail_run_cap; "
        "llm_cap; chunk_cap: https://x/a; listing errors: 1 (HTTP 500)"
    )
    assert completeness.cap_notes(status) == [
        "page_cap", "detail_cap", "detail_run_cap", "llm_cap", "chunk_cap"]  # fmt: skip
    assert completeness.cap_notes("detail_cap: 400 of 512 links") == ["detail_cap"]
    assert completeness.cap_notes("ok; geocode_cap: 60 of 61") == ["geocode_cap"]
    assert completeness.cap_notes("ok") == completeness.cap_notes(None) == []


def test_measure_and_annotation(tz):
    now = _now(tz)
    concerts = [
        _concert("2026-10-12T20:00:00+02:00", ["gancio:a", "json-ld"]),
        _concert("2026-12-08T20:00:00+01:00", ["gancio:a"]),  # 60 days: not beyond
        _concert("2026-12-09T20:00:00+01:00", ["gancio:a"]),  # 61 days: beyond
        _concert("2027-06-01T20:00:00+02:00", ["listing_jsonld:b"]),
    ]
    rows = [
        {"name": "Salle A", "reader": "listing_jsonld", "status": "detail_cap: 400 of 410 links",
         "last": "2027-06-01"},
        {"name": "Salle B", "reader": "page_llm", "status": "ok; chunks 3; detail_cap: 40",
         "last": "2026-12-20"},
        {"name": "Salle C", "reader": "wp_json", "status": "robots_blocked", "last": None},
    ]  # fmt: skip
    statuses = {"gancio": "ok; Ville Morte: detail_cap: 400 of 512", "ticketmaster": "skipped"}
    m = completeness.measure(concerts, rows, statuses, {"capped": True}, now, tz)
    assert m["by_source"] == {
        "gancio": {"concerts": 3, "beyond": 1, "last": "2026-12-09"},
        "json-ld": {"concerts": 1, "beyond": 0, "last": "2026-10-12"},
        "listing_jsonld": {"concerts": 1, "beyond": 1, "last": "2027-06-01"},
    }
    assert m["caps"] == {
        "detail_cap": {"gancio": 1, "listing_jsonld": 1, "page_llm": 1},
        "lookup_cap": {"artists": 1},
    }
    assert m["venues"][2] == {"name": "Salle C", "last": None, "caps": []}
    line = completeness.text(m)
    assert line == (
        "beyond 60d/concerts@last: gancio=1/3@2026-12-09 json-ld=0/1@2026-10-12 "
        "listing_jsonld=1/1@2027-06-01 | caps: detail_cap=3(gancio*1,listing_jsonld*1,"
        "page_llm*1) lookup_cap=1(artists*1) | Mes salles last read: "
        "Salle A@2027-06-01[detail_cap], Salle B@2026-12-20[detail_cap], Salle C@-"
    )
    # numbers, dates and configured venue names only: no event title or performer
    assert "Secret Title" not in line and "Act" not in line
    assert "Secret Title" not in json.dumps(m)
    assert completeness.text({"status": "error: KeyError"}) == "error: KeyError"
    assert completeness.text(None) == "-"
    md = "\n".join(completeness.summary_rows(m))
    assert "| … listing_jsonld | 1 / 1, 2027-06-01 |" in md
    assert "| … Salle B: farthest date read | 2026-12-20 (detail_cap) |" in md


def test_report_carries_completeness(tmp_path, zone, tz, fixture_text, monkeypatch):
    """End to end on the fixtures: the report has the measure, the summary its rows, and the
    pipeline's one-line annotation is unchanged (the measure is its own annotation)."""
    from nightcrawler.pipeline import run
    from nightcrawler.sources.osm import OVERPASS_URL

    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    with respx.mock:
        respx.post(OVERPASS_URL).respond(200, text=fixture_text("overpass.json"))
        respx.get("https://bulbe.example/robots.txt").respond(404)
        respx.get(host="bulbe.example", path="/").respond(200, html=fixture_text("home.html"))
        respx.get("https://bulbe.example/programmation/").respond(
            200, html=fixture_text("agenda.html")
        )
        respx.get("https://ombres.example/robots.txt").respond(404)
        respx.get(host="ombres.example", path="/").respond(200, html=fixture_text("microdata.html"))
        respx.get(host="api.deezer.com").respond(json={"data": []})
        respx.get(host="musicbrainz.org").respond(json={"artists": []})
        now = datetime(2026, 10, 5, 12, tzinfo=tz)
        report = run(zone, tmp_path, Fetcher(cache_dir=None, min_interval=0), now=now)
    m = report["completeness"]
    assert m["beyond_days"] == 60 and m["venues"] == [] and m["caps"] == {}
    assert sum(r["concerts"] for r in m["by_source"].values()) >= report["concerts"]
    assert "| Cap hits | none |" in summary_markdown(report)
    assert "beyond" not in one_line(report)
