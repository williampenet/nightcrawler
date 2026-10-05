import httpx
import respx

from nightcrawler.http import Fetcher
from nightcrawler.models import Venue
from nightcrawler.probe import agenda_candidates, ical_links, platforms_in, probe_venue


def venue(site="https://bulbe.example"):
    return Venue("osm:node/1", "Le Petit Bulbe", 45.75, 4.85, "music_venue", website=site)


def fetcher():
    return Fetcher(cache_dir=None, min_interval=0)


def test_agenda_candidates(fixture_text):
    found = agenda_candidates(fixture_text("home.html"), "https://bulbe.example/")
    assert found == ["https://bulbe.example/programmation/"]


def test_agenda_candidates_prefers_concert_pages():
    # Périscope: the billetterie is an empty JS app, /concerts/ is server-rendered
    html = (
        '<a href="/billetterie/">Billetterie</a><a href="/agenda/">Agenda</a>'
        '<a href="/concerts/">Programme</a><a href="/la-saison/">Nos concerts</a>'
    )
    found = agenda_candidates(html, "https://periscope.example/")
    assert set(found[:2]) == {
        "https://periscope.example/concerts/",
        "https://periscope.example/la-saison/",
    }
    assert set(found[2:]) == {
        "https://periscope.example/agenda/",
        "https://periscope.example/billetterie/",
    }


def test_agenda_candidates_past_and_word_boundaries():
    html = (
        '<a href="/concerts-passes/">Concerts passés</a><a href="/archives/">Agenda archives</a>'
        '<a href="/concertation/">Agenda</a><a href="/concerts/">Concerts</a>'
    )
    found = agenda_candidates(html, "https://x.example/")
    assert found[:2] == ["https://x.example/concerts/", "https://x.example/concertation/"]
    assert set(found[2:]) == {"https://x.example/archives/", "https://x.example/concerts-passes/"}


def test_platforms(fixture_text):
    assert platforms_in(fixture_text("home.html")) == ["shotgun"]


def test_ical_links_tribe():
    html = '<div class="tribe-events"></div><a href="webcal://x.example/cal.ics">ics</a>'
    assert ical_links(html, "https://x.example/agenda/") == ["https://x.example/cal.ics"]


@respx.mock
def test_probe_finds_jsonld(fixture_text, tz):
    respx.get("https://bulbe.example/robots.txt").respond(404)
    respx.get(host="bulbe.example", path="/").respond(200, html=fixture_text("home.html"))
    respx.get("https://bulbe.example/programmation/").respond(200, html=fixture_text("agenda.html"))
    probe, events = probe_venue(venue(), fetcher(), tz)
    assert probe.status == "structured" and probe.method == "json-ld"
    assert probe.agenda_url == "https://bulbe.example/programmation/"
    assert probe.platforms == ["shotgun"]
    assert len(events) == 2


@respx.mock
def test_probe_falls_back_to_ical(fixture_text, tz):
    respx.get("https://ombres.example/robots.txt").respond(404)
    respx.get(host="ombres.example", path="/").respond(
        200, html='<a href="/agenda.ics">Calendrier</a>'
    )
    respx.get("https://ombres.example/agenda.ics").respond(
        200, text=fixture_text("agenda.ics"), headers={"content-type": "text/calendar"}
    )
    probe, events = probe_venue(venue("https://ombres.example"), fetcher(), tz)
    assert probe.method == "ical" and len(events) == 1


@respx.mock
def test_probe_respects_robots(tz):
    respx.get("https://bulbe.example/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /\n", headers={"content-type": "text/plain"}
    )
    probe, _ = probe_venue(venue(), fetcher(), tz)
    assert probe.status == "robots_blocked"


@respx.mock
def test_probe_errors(tz):
    respx.get("https://bulbe.example/robots.txt").respond(404)
    respx.get(host="bulbe.example", path="/").mock(side_effect=httpx.ConnectError("down"))
    probe, _ = probe_venue(venue(), fetcher(), tz)
    assert probe.status == "fetch_error"
    assert probe_venue(venue(None), fetcher(), tz)[0].status == "no_website"


@respx.mock
def test_probe_no_agenda(tz):
    respx.get("https://bulbe.example/robots.txt").respond(404)
    respx.get(host="bulbe.example", path="/").respond(200, html="<p>Bienvenue</p>")
    probe, events = probe_venue(venue(), fetcher(), tz)
    assert probe.status == "no_agenda" and events == []
