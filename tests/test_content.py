"""What the judge can read per concert (WIP-89): counts per source family, nothing else."""

import json

from nightcrawler import content
from nightcrawler.artists import Artist
from nightcrawler.models import Concert

SECRET = "Une présentation de la salle qui ne doit pas sortir du store. " * 6


def concert(cid, title, sources, lineup=(), artists=()):
    return Concert(id=cid, title=title, start="2026-10-10T20:00:00+02:00", venue_id="v",
                   venue_name="Le Transbordeur", url=None, ticket_url=None, performers=[],
                   sources=list(sources), reason="r", artists=list(artists),
                   lineup=list(lineup))  # fmt: skip


CONCERTS = [
    # the listing repeats its title as the only act: no line-up, title only
    concert("a", "La Nuit du Gouyad 2", ["ticketmaster"], lineup=["LA NUIT DU GOUYAD 2"]),
    concert(
        "b",
        "Earth",
        ["gancio:mobilizon.example", "json-ld"],
        lineup=["Earth", "Boris"],
        artists=["earth", "boris"],
    ),  # fmt: skip
    concert("c", "Soirée", ["platform:shotgun"]),
]
ARTISTS = {"earth": Artist(key="earth", name="Earth", confident=True),
           "boris": Artist(key="boris", name="Boris")}  # fmt: skip


def test_measure_per_source_family():
    m = content.measure(CONCERTS, ARTISTS, {"b": SECRET, "c": "Court."})
    assert m["descriptions"] == "store"
    assert m["all"] == {"concerts": 3, "lineup": 1, "identified": 1, "description": 2,
                        "long_description": 1, "title_only": 1}  # fmt: skip
    assert list(m["by_source"]) == ["gancio", "json-ld", "platform:shotgun", "ticketmaster"]
    assert m["by_source"]["ticketmaster"]["title_only"] == 1
    assert m["by_source"]["platform:shotgun"] == {"concerts": 1, "lineup": 0, "identified": 0,
                                                  "description": 1, "long_description": 0,
                                                  "title_only": 0}  # fmt: skip
    line = content.text(m)
    assert line.startswith("descriptions=store all(concerts=3 lineup=1")
    assert "ticketmaster(concerts=1 lineup=0 identified=0 description=0" in line
    # counts only: no text, title or id leaves the measure
    out = json.dumps(m) + line
    assert "présentation" not in out and "Gouyad" not in out and "Earth" not in out


def test_without_the_store_descriptions_are_left_out():
    m = content.measure(CONCERTS, ARTISTS, None)
    assert m["descriptions"] == "off"
    assert m["all"] == {"concerts": 3, "lineup": 1, "identified": 1}
    assert "title_only" not in content.text(m)
    assert content.text(None) == "-" and content.text({"status": "error: X"}) == "error: X"


def test_family_and_lineup():
    assert content.family("gancio:host.example") == "gancio"
    assert content.family("platform:shotgun") == "platform:shotgun"
    assert content.family("ticketmaster") == "ticketmaster"
    assert not content.has_lineup(CONCERTS[0])
    assert content.has_lineup(concert("d", "Soirée", [], lineup=["Soirée", "DJ Bob"]))
