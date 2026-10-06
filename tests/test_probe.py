import httpx
import respx

from nightcrawler.http import Fetcher
from nightcrawler.models import Venue
from nightcrawler.pipeline import platform_stats
from nightcrawler.probe import (
    PlatformBudget,
    agenda_candidates,
    ical_links,
    platform_links,
    platforms_in,
    probe_venue,
)


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


# -- ticketing platform pages (WIP-37) ---------------------------------------------

SHOTGUN = "https://shotgun.live/fr/venues/theatre-des-ombres"
DICE = "https://dice.fm/venue/theatre-des-ombres-x7k2"
HELLOASSO = "https://www.helloasso.com/associations/ombres/evenements/saison"


def test_platform_links_specific_pages_only(fixture_text):
    links = platform_links(fixture_text("platform_venue.html"), "https://ombres.example/")
    # the shotgun homepage and the lookalike domain are ignored; document order is kept
    assert links == [("shotgun", SHOTGUN), ("dice", DICE), ("helloasso", HELLOASSO)]
    html = '<a href="https://www.facebook.com/events/123/">fb</a><a href="https://dice.fm/fr">x</a>'
    assert platform_links(html, "https://x.example/") == [
        ("facebook-events", "https://www.facebook.com/events/123/")
    ]


def _platform_mocks(fixture_text):
    respx.get("https://ombres.example/robots.txt").respond(404)
    respx.get(host="ombres.example", path="/").respond(
        200, html=fixture_text("platform_venue.html")
    )
    respx.get("https://shotgun.live/robots.txt").respond(404)
    respx.get(SHOTGUN).respond(200, html=fixture_text("shotgun_venue.html"))
    respx.get("https://dice.fm/robots.txt").respond(404)
    respx.get(DICE).respond(200, html="<html><body><div id='root'></div></body></html>")


@respx.mock
def test_probe_reads_platform_pages(fixture_text, tz):
    _platform_mocks(fixture_text)
    helloasso = respx.get(HELLOASSO).respond(200, html="")
    probe, events = probe_venue(venue("https://ombres.example"), fetcher(), tz)
    assert probe.status == "structured" and probe.method == "platform:shotgun"
    assert probe.agenda_url == SHOTGUN and probe.events_found == 2
    assert [(p["platform"], p["status"], p["events"]) for p in probe.platform_pages] == [
        ("shotgun", "events", 2),
        ("dice", "no_events", 0),
    ]
    assert not helloasso.called  # at most 2 platform pages per venue
    assert {e.source for e in events} == {"platform:shotgun"}
    assert {e.venue_id for e in events} == {"osm:node/1"}  # the venue that linked it
    # the event's own URL is kept; without one, the platform page stands in
    assert [e.url for e in events] == ["https://shotgun.live/fr/events/kraut-tuesday", SHOTGUN]


@respx.mock
def test_probe_platform_robots_blocked(tz):
    respx.get("https://ombres.example/robots.txt").respond(404)
    respx.get(host="ombres.example", path="/").respond(200, html=f'<a href="{HELLOASSO}">x</a>')
    respx.get("https://www.helloasso.com/robots.txt").respond(
        200,
        text="User-agent: *\nDisallow: /associations/\n",
        headers={"content-type": "text/plain"},
    )
    page = respx.get(HELLOASSO).respond(200, html="")
    budget = PlatformBudget(5)
    probe, events = probe_venue(venue("https://ombres.example"), fetcher(), tz, budget)
    assert probe.status == "platform_only" and events == []
    assert probe.platform_pages[0]["status"] == "robots_blocked"
    assert not page.called and budget.left == 5  # never bypassed, costs no budget
    assert platform_stats([probe]) == {
        "helloasso": {"pages": 0, "with_events": 0, "events": 0, "robots_blocked": 1}
    }


@respx.mock
def test_probe_platform_budget(fixture_text, tz):
    _platform_mocks(fixture_text)
    probe, events = probe_venue(venue("https://ombres.example"), fetcher(), tz, PlatformBudget(0))
    assert probe.status == "platform_only" and events == []
    assert {p["status"] for p in probe.platform_pages} == {"skipped_budget"}
    assert platform_stats([probe]) == {}  # budget skips are not counted as pages
