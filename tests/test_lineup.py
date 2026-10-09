"""Every act of an evening (WIP-72): line-up from performers, titles and descriptions."""

import json
from dataclasses import replace
from datetime import datetime

import pytest
import respx

from nightcrawler.artists import DEEZER_SEARCH, MB_SEARCH, enrich
from nightcrawler.events import build_concerts
from nightcrawler.http import Fetcher
from nightcrawler.lineup import description_acts, merge_lineup, parse_title, split_title
from nightcrawler.models import RawEvent, Venue
from nightcrawler.sources import gancio
from nightcrawler.structured import event_from_schema, jsonld_events

NOW = datetime(2026, 10, 5, 12)
MARCHE_GARE = Venue("cfg:marchegare", "Marché Gare", 45.7484, 4.8263, "music_venue")
MARQUISE = Venue("osm:marquise", "La Marquise", 45.7480, 4.8460, "music_venue")


def build(raw, tz, venues=(MARCHE_GARE, MARQUISE)):
    now = NOW.replace(tzinfo=tz)
    return build_concerts(raw, {v.id: v for v in venues}, now=now, window_days=60, tz=tz)


def fetcher():
    return Fetcher(cache_dir=None, min_interval=0)


# Real titles from the fixtures (gancio_events.json, listing_jsonld/, coverage/,
# test_dedup.py) and from agenda.villemorte.fr (WIP-72 ticket)
@pytest.mark.parametrize(
    "title, acts",
    [
        ("Tomoyuki Aoki & Harutaka Mochizuki", ["Tomoyuki Aoki", "Harutaka Mochizuki"]),
        (
            "Lucio Bukowski + Anton Serra + OSter Lapwass (nouvelle date)",
            ["Lucio Bukowski", "Anton Serra", "OSter Lapwass"],
        ),
        ("DazzlingKillmen (Us) + Pord + Comte Zero", ["DazzlingKillmen", "Pord", "Comte Zero"]),
        ('Leïla Martial & Elie Dufour "Karma Bazar"', ["Leïla Martial", "Elie Dufour"]),
        ("Release party : Pord - première partie Comte Zero", ["Pord", "Comte Zero"]),
        ("The Molotovs + 1Ere Partie", ["The Molotovs"]),
        ("Hania Rani + guests (COMPLET)", ["Hania Rani"]),
        ("Teenage Fanclub + guest", ["Teenage Fanclub"]),
        ("Bérurier Noir - Sold out", ["Bérurier Noir"]),
        ("DJ Krush (live)", ["DJ Krush"]),
        ("Concert : Earth", ["Earth"]),
        ("Soirée Bruits Blancs : Pord x Comte Zero b2b Ana", ["Pord", "Comte Zero", "Ana"]),
        ("Nuit noise : Pord + Comte Zero", ["Pord", "Comte Zero"]),
        ("Pord & Ana : tournée d'adieu", ["Pord", "Ana"]),
        # "X : A, B" without a hard separator stays one act: often "Artist : tour name"
        ("Drone Night: Earth, Boris (complet)", ["Drone Night: Earth, Boris"]),
        ("Pord : Tournée Rouge & Noir", ["Pord : Tournée Rouge & Noir"]),
        ("Hania Rani : Ghosts, live", ["Hania Rani : Ghosts, live"]),
        # prices, times, statuses and placeholders are not acts (review of PR #61)
        ("Pord / 12€ / 20h", ["Pord"]),
        ("Concert : Pord / 19h30", ["Pord"]),
        ("Pord, 20h30, 12€", ["Pord"]),
        ("Pord + Chevignon + Complet", ["Pord", "Chevignon"]),
        ("Pord + Chevignon + Sold out", ["Pord", "Chevignon"]),
        ("Pord, Chevignon (Lyon) / Gratuit", ["Pord", "Chevignon"]),
        ("Pord + Chevignon - 18:00", ["Pord", "Chevignon"]),
        ("Ana + Bob - 20h30 - 12€", ["Ana", "Bob"]),
        ("Ana + Bob - 12€", ["Ana", "Bob"]),
        ("Pord + Chevignon + Annulé", ["Pord", "Chevignon"]),
        ("Pord + Entrée libre", ["Pord"]),
        ("Pord + DJ set", ["Pord"]),
        ("Pord x 2 soirées", ["Pord"]),
        # known limit: " / " always splits; the artist lookup tries the whole title first
        ("AC / DC tribute", ["AC", "DC tribute"]),
        # one act: an "Artist : album" title, a subtitle, a capital X inside a name
        ("Earth : Full Upon Her Burning Lips", ["Earth : Full Upon Her Burning Lips"]),
        ("Philippe Katerine - Aux anges", ["Philippe Katerine - Aux anges"]),
        ("Malcolm X Band", ["Malcolm X Band"]),
        ("Sunn O))) (US) / Boris", ["Sunn O)))", "Boris"]),
        # separators inside a descriptor do not split
        ("Pord (noise / rock, Lyon) + Comte Zero", ["Pord", "Comte Zero"]),
    ],
)
def test_split_title(title, acts):
    assert split_title(title) == acts


def test_a_name_given_whole_by_a_source_is_not_split():
    assert split_title("Simon & Garfunkel + Pord", {"simongarfunkel"}) == [
        "Simon & Garfunkel",
        "Pord",
    ]
    assert parse_title("Earth, Wind & Fire")[1] == [["Earth", "Wind", "Fire"]]  # see enrich


def test_description_first_paragraph_conservative():
    # first paragraph of agenda.villemorte.fr/event/tomoyuki-aoki-and-harutaka-mochizuki
    html = (
        "<p>Tomoyuki Aoki &amp; Harutaka Mochizuki (avant-garde/free rock, Japon)</p>"
        "<p>Ouverture des portes 20h, prix libre.</p>"
    )
    assert description_acts(html) == ["Tomoyuki Aoki & Harutaka Mochizuki"]
    assert description_acts("<p>Pord (noise rock, Lyon)<br>Comte Zero (post-punk, FR)</p>") == [
        "Pord",
        "Comte Zero",
    ]
    # one line that is not "Name (genre, country)": nothing is taken
    assert description_acts("<p>Pord (noise rock, Lyon)<br>Soirée de soutien</p>") == []
    assert description_acts("<p>Soirée noise rock</p>") == []
    assert description_acts("<p>Pord (Lyon)</p>") == []  # no "genre, country"
    assert description_acts(None) == [] and description_acts("") == []
    many = "<br>".join(f"Band {i} (rock, FR)" for i in range(9))
    assert description_acts(f"<p>{many}</p>") == []


@respx.mock
def test_gancio_a_and_b(zone, tz):
    """The PM's example (2026-10-07): Gancio title "A & B", no performers.

    Measured by the orchestrator: title and first description paragraph below; the live
    event has `performers: []`. Slug from the event URL; id, place and date are placeholders
    in the shape of tests/fixtures/gancio_events.json; no tag, so the detail is fetched.
    """
    base = "https://agenda.villemorte.fr"
    item = {
        "id": 901,
        "title": "Tomoyuki Aoki & Harutaka Mochizuki",
        "slug": "tomoyuki-aoki-and-harutaka-mochizuki",
        "start_datetime": int(datetime(2026, 10, 20, 20, 30, tzinfo=tz).timestamp()),
        "end_datetime": None,
        "media": [],
        "tags": [],
        "place": {
            "id": 1,
            "name": "Grrrnd Zero",
            "address": "60, avenue de Bohlen 69120, Vaulx en Velin",
            "latitude": 45.7695,
            "longitude": 4.9183,
        },
    }
    description = (
        "<p>Tomoyuki Aoki &amp; Harutaka Mochizuki (avant-garde/free rock, Japon)</p>"
        "<p>Concert à prix libre.</p>"
    )
    respx.get(base + "/robots.txt").respond(200, text="user-agent: *\nallow: /\n")
    respx.get(base + "/api/events").respond(200, json=[item])
    respx.get(base + "/api/event/detail/tomoyuki-aoki-and-harutaka-mochizuki").respond(
        200, json={"description": description}
    )
    z = replace(zone, gancio_instances=({"name": "Ville Morte", "url": base},))
    f = fetcher()
    venues, events, status = gancio.collect(z, f, NOW.replace(tzinfo=tz), tz)
    assert gancio.fetch_details(z, f, events) == []
    assert status == "ok" and events[0].performers == []
    assert events[0].billed == ["Tomoyuki Aoki & Harutaka Mochizuki"]
    (c,) = build(events, tz, venues)
    assert c.lineup == ["Tomoyuki Aoki", "Harutaka Mochizuki"]
    # the title alone gives the same line-up (an event whose detail is not fetched)
    (c2,) = build([replace(events[0], billed=[])], tz, venues)
    assert c2.lineup == ["Tomoyuki Aoki", "Harutaka Mochizuki"]


def test_a_plus_b_plus_c_from_venue_jsonld(tz, fixture_text):
    html = fixture_text("listing_jsonld/marchegare_lucio.html")
    events = jsonld_events(html, MARCHE_GARE.id, tz)
    assert events[0].performers == []
    (c,) = build(events, tz)
    assert c.lineup == ["Lucio Bukowski", "Anton Serra", "OSter Lapwass"]


def _tm(title, performers):
    return RawEvent(
        title=title,
        start=datetime.fromisoformat("2026-10-10T20:00:00+02:00"),
        source="ticketmaster",
        venue_id=MARQUISE.id,
        url="https://www.ticketmaster.fr/earth-marquise",
        performers=performers,
    )


def test_ticketmaster_and_venue_jsonld_with_support_act(tz):
    # attractions as ticketmaster.py reads them; the venue's JSON-LD also names the support
    venue_node = {
        "@type": "MusicEvent",
        "name": "Earth",
        "startDate": "2026-10-10T19:30:00+02:00",
        "url": "https://lamarquise.example/agenda/earth",
        "performer": [
            {"@type": "MusicGroup", "name": "Earth"},
            {"@type": "MusicGroup", "name": "Sunn O)))"},
        ],
    }
    venue = event_from_schema(venue_node, MARQUISE.id, "json-ld", tz)
    (c,) = build([_tm("Earth - European Tour 2026", ["Earth"]), venue], tz)
    assert c.sources == ["json-ld", "ticketmaster"]
    assert c.lineup == ["Earth", "Sunn O)))"]  # union, venue (best source) first, no duplicate


def test_title_split_joins_performers_only_when_it_shares_one():
    tm = ("Earth - European Tour 2026", ["Earth"], [])
    # the venue page has no performers: its "A + B" title names the support act
    assert merge_lineup([("Earth + Sunn O)))", [], []), tm]) == ["Earth", "Sunn O)))"]
    # an "Act - Tour" title shares no performer: not a line-up
    assert merge_lineup([("European Tour 2026 + Ana", [], []), tm]) == ["Earth"]
    # placeholders and duplicates never become acts
    assert merge_lineup([("x", ["Earth", "EARTH", "Special Guest", "TBA"], [])]) == ["Earth"]


def test_ampersand_band_given_whole_by_a_source_stays_whole(tz):
    venue = RawEvent(
        title="Simon & Garfunkel + Pord",
        start=datetime.fromisoformat("2026-10-10T20:00:00+02:00"),
        source="json-ld",
        venue_id=MARQUISE.id,
    )
    (c,) = build([venue, _tm("Simon & Garfunkel", ["Simon & Garfunkel"])], tz)
    assert c.lineup == ["Simon & Garfunkel", "Pord"]


def _deezer(name, artist_id, fans=5000):
    respx.get(DEEZER_SEARCH, params={"q": name}).respond(
        json={"data": [{"id": artist_id, "name": name, "nb_fan": fans}]}
    )


@respx.mock
def test_ampersand_band_known_to_artist_identification_stays_whole(tz):
    _deezer("Earth, Wind & Fire", 5, 900_000)
    _deezer("Simon & Garfunkel", 6, 900_000)
    _deezer("Pord", 7, 900_000)
    respx.get(DEEZER_SEARCH).respond(json={"data": []})  # anything else: unknown
    respx.get(url__regex=r"https://api\.deezer\.com/artist/\d+/related").respond(json={"data": []})
    respx.get(MB_SEARCH).respond(json={"artists": []})

    def concert(title):
        ev = RawEvent(
            title=title,
            start=datetime.fromisoformat("2026-10-10T20:00:00+02:00"),
            source="json-ld",
            venue_id=MARQUISE.id,
        )
        return build([ev], tz)[0]

    ewf, sg = concert("Earth, Wind & Fire"), concert("Simon & Garfunkel + Pord")
    assert ewf.lineup == ["Earth", "Wind", "Fire"] and sg.lineup == ["Simon", "Garfunkel", "Pord"]
    enrich([ewf, sg], fetcher())
    assert ewf.lineup == ["Earth, Wind & Fire"] and ewf.artists == ["earthwindfire"]
    assert sg.lineup == ["Simon & Garfunkel", "Pord"]
    assert sg.artists == ["simongarfunkel", "pord"]


@respx.mock
def test_identification_runs_on_every_act(tz):
    _deezer("Tomoyuki Aoki", 11)
    _deezer("Harutaka Mochizuki", 12)
    route = respx.get(DEEZER_SEARCH).respond(json={"data": []})  # the whole title: unknown
    respx.get(url__regex=r"https://api\.deezer\.com/artist/\d+/related").respond(json={"data": []})
    respx.get(MB_SEARCH).respond(json={"artists": []})
    ev = RawEvent(
        title="Tomoyuki Aoki & Harutaka Mochizuki",
        start=datetime.fromisoformat("2026-10-20T20:30:00+02:00"),
        source="gancio:agenda.villemorte.fr",
        venue_id=MARQUISE.id,
        tags=["concert"],
    )
    (c,) = build([ev], tz)
    artists, stats = enrich([c], fetcher())
    assert c.artists == ["tomoyukiaoki", "harutakamochizuki"]
    assert c.lineup == ["Tomoyuki Aoki", "Harutaka Mochizuki"]
    # whole title first (unknown), then each act: 3 searches, no more
    assert route.call_count == 1 and stats["candidates"] == 3


@respx.mock
def test_artist_and_tour_name_title_still_finds_the_artist(tz):
    """One-act line-up: enrich() takes the per-title path of main (performers_of)."""
    _deezer("Pord", 7)
    _deezer("Hania Rani", 8)
    respx.get(DEEZER_SEARCH).respond(json={"data": []})
    respx.get(url__regex=r"https://api\.deezer\.com/artist/\d+/related").respond(json={"data": []})
    respx.get(MB_SEARCH).respond(json={"artists": []})
    events = [
        RawEvent(
            title=t,
            start=datetime.fromisoformat(f"2026-10-1{i}T20:00:00+02:00"),
            source="json-ld",
            venue_id=MARQUISE.id,
        )
        for i, t in enumerate(["Pord : Tournée Rouge & Noir", "Hania Rani : Ghosts, live"])
    ]
    pord, hania = build(events, tz)
    enrich([pord, hania], fetcher())
    assert len(pord.lineup) == 1 and pord.artists == ["pord"]
    assert len(hania.lineup) == 1 and hania.artists == ["haniarani"]


def test_lookup_cap_still_applies(tz):
    ev = RawEvent(
        title="Pord + Comte Zero + Ana",
        start=datetime.fromisoformat("2026-10-10T20:00:00+02:00"),
        source="json-ld",
        venue_id=MARQUISE.id,
    )
    (c,) = build([ev], tz)
    artists, stats = enrich([c], fetcher(), max_lookups=0)
    assert stats["capped"] is True and artists == {} and c.artists == []


def test_lineup_in_concert_json(tz):
    (c,) = build(jsonld_events(_lucio_html(), MARCHE_GARE.id, tz), tz)
    assert json.loads(json.dumps(c.to_dict()))["lineup"] == c.lineup


def _lucio_html():
    node = {
        "@type": "Event",
        "name": "Lucio Bukowski + Anton Serra + OSter Lapwass (nouvelle date)",
        "startDate": "2026-10-08T20:00:00+02:00",
    }
    return f'<script type="application/ld+json">{json.dumps(node)}</script>'
