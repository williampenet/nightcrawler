"""Duplicate concerts across sources (WIP-42), with hand-written listings."""

from datetime import datetime

import pytest

from nightcrawler.dedup import clean_title, concert_id
from nightcrawler.events import build_concerts
from nightcrawler.models import RawEvent, Venue

MARQUISE = Venue("osm:marquise", "La Marquise", 45.7480, 4.8460, "music_venue")
NEAR = Venue("tm:quai", "Péniche voisine", 45.7495, 4.8475, "music_venue")  # ~200 m
RAYONNE = Venue("tm:rayonne", "La Rayonne", 45.7570, 4.8460, "music_venue")  # ~1 km
NOW = datetime(2026, 10, 5, 12)


def ev(title, venue, source, day=10, hour=19, minute=30, **kw):
    start = datetime.fromisoformat(f"2026-10-{day:02d}T{hour:02d}:{minute:02d}:00+02:00")
    return RawEvent(title=title, start=start, source=source, venue_id=venue, **kw)


def molotovs(third_venue):
    return [
        ev(
            "The Molotovs + 1Ere Partie",
            "osm:marquise",
            "ticketmaster",
            url="https://www.ticketmaster.fr/molotovs-marquise",
            ticket_url="https://www.ticketmaster.fr/molotovs-marquise",
            performers=["The Molotovs"],
        ),
        ev(
            "THE MOLOTOVS - THE MOLOTOVS 1ere partie",
            "osm:marquise",
            "json-ld",
            hour=20,
            minute=0,
            url="https://lamarquise.example/agenda/molotovs",
        ),
        ev(
            "The Molotovs + 1Ere Partie",
            third_venue,
            "ticketmaster",
            url="https://www.ticketmaster.fr/molotovs-other",
            ticket_url="https://www.ticketmaster.fr/molotovs-other",
            performers=["The Molotovs"],
        ),
    ]


def build(raw, tz, venues=(MARQUISE, NEAR, RAYONNE)):
    stats: dict = {}
    now = NOW.replace(tzinfo=tz)
    out = build_concerts(
        raw, {v.id: v for v in venues}, now=now, window_days=60, tz=tz, stats=stats
    )
    return out, stats


@pytest.mark.parametrize(
    "title, performers, expected",
    [
        ("THE MOLOTOVS - THE MOLOTOVS 1ere partie", [], "the molotovs"),
        ("The Molotovs + 1Ère Partie", [], "the molotovs"),
        ("Hania Rani + guests (COMPLET)", [], "hania rani"),
        ("Bérurier Noir - Sold out", [], "berurier noir"),
        ("Concert : Earth", [], "earth"),
        ("Earth : Full Upon Her Burning Lips", ["Earth"], "earth"),
        ("Boris - World Tour 2026", [], "boris"),
        ("Release party : Pord - première partie Comte Zero", [], "pord comte zero"),
        ("Fanfare annulée", [], "fanfare"),
    ],
)
def test_clean_title(title, performers, expected):
    assert clean_title(title, performers) == expected


def test_molotovs_far_venue_is_a_conflict(tz):
    concerts, stats = build(molotovs("tm:rayonne"), tz)
    assert [c.venue_name for c in concerts] == ["La Marquise", "La Rayonne"]
    marquise = concerts[0]
    assert marquise.title == "THE MOLOTOVS - THE MOLOTOVS 1ere partie"  # venue site wins
    assert marquise.start == "2026-10-10T19:30:00+02:00"  # earliest known time
    assert marquise.sources == ["json-ld", "ticketmaster"]  # best source first
    assert marquise.links == [
        {"label": "Page", "url": "https://lamarquise.example/agenda/molotovs"},
        {"label": "Billets", "url": "https://www.ticketmaster.fr/molotovs-marquise"},
    ]
    assert marquise.url == "https://lamarquise.example/agenda/molotovs"
    assert marquise.ticket_url == "https://www.ticketmaster.fr/molotovs-marquise"
    assert marquise.performers == ["The Molotovs"]
    assert stats["merged"] == 1 and stats["conflicts"] == 1
    assert stats["conflict_examples"][0]["venues"] == ["La Marquise", "La Rayonne"]
    assert len(stats["merge_examples"]) == 1


def test_bare_place_is_never_a_conflict(tz):
    raw = [
        ev("Earth", "osm:marquise", "json-ld"),
        ev("Concert : Earth", "place:ailleurs", "json-ld"),
    ]
    concerts, stats = build(raw, tz)
    assert len(concerts) == 2 and stats["conflicts"] == 0


def test_molotovs_near_venue_is_merged(tz):
    concerts, stats = build(molotovs("tm:quai"), tz)
    assert len(concerts) == 1
    assert [x["url"] for x in concerts[0].links] == [
        "https://lamarquise.example/agenda/molotovs",
        "https://www.ticketmaster.fr/molotovs-marquise",
        "https://www.ticketmaster.fr/molotovs-other",
    ]
    assert concerts[0].links[2]["label"] == "www.ticketmaster.fr"  # "Billets" already taken
    assert concerts[0].venue_id == "osm:marquise"
    assert stats["merged"] == 2 and stats["conflicts"] == 0


def test_ids_of_each_source_survive_as_aliases(tz):
    site, tm = molotovs("tm:rayonne")[1], molotovs("tm:rayonne")[0]
    site.title = "The Molotovs (UK) - Release Tour"  # titles differ: so do lone ids
    site_alone, _ = build([site], tz)
    tm_alone, _ = build([tm], tz)
    merged, _ = build([tm, site], tz)
    assert merged[0].id == site_alone[0].id == concert_id(merged[0])  # best source's id
    assert tm_alone[0].id != merged[0].id and merged[0].aliases == [tm_alone[0].id]
    assert tm_alone[0].aliases == []


@pytest.mark.parametrize(
    "titles, expected",
    [
        # festival acts share the festival name, with or without an umbrella listing
        (
            [
                "Nuits Sonores@0",
                "Nuits Sonores : Jeff Mills@18",
                "Nuits Sonores : Nina Kraviz@22",
                "Nuits Sonores : Ben UFO@23",
            ],
            4,
        ),
        (
            [
                "Nuits Sonores : Jeff Mills@18",
                "Nuits Sonores : Nina Kraviz@19",
                "Nuits Sonores : Ben UFO@20",
            ],
            3,
        ),
        (["Earth@19", "Earth + Boris@20:15", "Boris@21:40"], 2),  # no chaining
        (["Earth@0", "Earth@18", "Earth@22"], 2),  # unknown time joins one group only
        (["Jazz@20", "Jazz Manouche Quartet@21"], 2),
        (["Jam Session Jazz@20", "Jam Session Funk@21"], 2),
        (["Hommage à Brel@20", "Hommage à Brassens@20:30"], 2),
        (["Tribute Queen@20", "Tribute ABBA@21"], 2),
        (["Blues Brothers@20", "Blues Pills@20:30"], 2),
        (["Les Wampas - Tournée 2026@20", "Les Wampas@20"], 1),
        (["Boris + Earth@20", "Earth@20"], 1),  # headliner + combined bill
        (["Jam Session Jazz@20", "Jam session jazz@20"], 1),  # same generic title
        (["Earth@23:30", "Earth@0:30+1"], 2),  # different calendar days
    ],
)
def test_grouping(tz, titles, expected):
    raw = []
    for spec in titles:
        title, when = spec.split("@")
        day = 11 if when.endswith("+1") else 10
        hour, _, minute = when.removesuffix("+1").partition(":")
        raw.append(ev(title, "osm:marquise", "json-ld", day, int(hour), int(minute or 0)))
    concerts, _ = build(raw, tz)
    assert len(concerts) == expected


def test_generic_performer_is_no_evidence(tz):
    raw = [
        ev("A night", "osm:marquise", "ticketmaster", performers=["Various Artists"]),
        ev("B party", "osm:marquise", "ticketmaster", performers=["Various Artists"]),
    ]
    assert len(build(raw, tz)[0]) == 2


def test_different_acts_same_night_are_kept(tz):
    raw = [
        ev("Earth", "osm:marquise", "json-ld", hour=20),
        ev("Boris + guests", "osm:marquise", "ticketmaster", hour=20, minute=30),
        ev("Drone Night", "osm:marquise", "json-ld", hour=23),
        ev("Techno Night", "osm:marquise", "ticketmaster", hour=23),
    ]
    concerts, stats = build(raw, tz)
    assert len(concerts) == 4 and stats["merged"] == 0 and stats["conflicts"] == 0


def test_same_act_two_days_or_far_apart_in_time(tz):
    raw = [
        ev("The Molotovs", "osm:marquise", "json-ld", day=10),
        ev("The Molotovs", "osm:marquise", "json-ld", day=11),
        ev("The Molotovs", "osm:marquise", "ticketmaster", day=11, hour=23),  # 3h30 later
    ]
    concerts, stats = build(raw, tz)
    assert len(concerts) == 3 and stats["merged"] == 0
    assert len({c.id for c in concerts}) == 3


def test_unknown_time_matches_any_time(tz):
    raw = [
        ev("Les Mains Froides", "osm:marquise", "ical", hour=0, minute=0),
        ev("Les Mains Froides", "osm:marquise", "gancio:agenda.example", hour=21, minute=0),
    ]
    concerts, stats = build(raw, tz)
    assert len(concerts) == 1 and concerts[0].start.endswith("T21:00:00+02:00")
    assert concerts[0].sources == ["ical", "gancio:agenda.example"]
