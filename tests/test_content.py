"""What the judge can read per concert (WIP-89): counts per source family, nothing else."""

import json
import os
from urllib.parse import urlsplit

import pytest

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
    assert line == ("descriptions=store cols=concerts/lineup/identified/desc/desc300/title_only "
                    "all=3/1/1/2/1/1 gancio=1/1/1/1/1/0 json-ld=1/1/1/1/1/0 "
                    "platform:shotgun=1/0/0/1/0/0 ticketmaster=1/0/0/0/0/1")  # fmt: skip
    rows = content.summary_rows(m)
    assert rows[0].startswith("| What the judge reads: concerts / lineup") and len(rows) == 6
    assert rows[-1] == "| … ticketmaster | 1 / 0 / 0 / 0 / 0 / 1 |"
    # counts only: no text, title or id leaves the measure
    out = json.dumps(m) + line
    assert "présentation" not in out and "Gouyad" not in out and "Earth" not in out


def test_without_the_store_descriptions_are_left_out():
    m = content.measure(CONCERTS, ARTISTS, None)
    assert m["descriptions"] == "off"
    assert m["all"] == {"concerts": 3, "lineup": 1, "identified": 1}
    assert "title_only" not in content.text(m)
    assert content.text(None) == "-" and content.text({"status": "error: X"}) == "error: X"
    assert content.summary_rows({"status": "error: X"}) == [
        "| What the judge reads (WIP-89) | error: X |"]  # fmt: skip
    failed = content.measure(CONCERTS, ARTISTS, None, "error: OperationalError")
    assert failed["descriptions"] == "error: OperationalError"  # not mistaken for "off"


def test_counts_follow_what_the_prompt_shows():
    """Line-up as judge.acts shows it (title excluded, non-Latin names kept), descriptions as
    judge.description_text reads them (markers and blanks stripped), the first 8 artists."""
    kanji = concert("e", "東京", ["json-ld"], lineup=["東京", "ボリス"])
    assert content.has_lineup(kanji)  # norm() drops these characters; the prompt keeps them
    long_title = "Soirée " * 25  # 175 characters, repeated as the only act
    assert not content.has_lineup(concert("g", long_title, [], lineup=[long_title]))
    m = content.measure([kanji], ARTISTS, {"e": "<<< >>>  "})
    assert m["all"]["description"] == 0  # nothing left once cleaned
    late = concert("f", "Fest", ["json-ld"], artists=[f"x{i}" for i in range(8)] + ["earth"])
    assert content.measure([late], ARTISTS, {})["all"]["identified"] == 0


def test_family_and_lineup():
    assert content.family("gancio:host.example") == "gancio"
    assert content.family("platform:shotgun") == "platform:shotgun"
    assert content.family("ticketmaster") == "ticketmaster"
    assert not content.has_lineup(CONCERTS[0])
    assert content.has_lineup(concert("d", "Soirée", [], lineup=["Soirée", "DJ Bob"]))


@pytest.mark.skipif(
    urlsplit(os.environ.get("TEST_DATABASE_URL", "")).hostname not in ("localhost", "127.0.0.1"),
    reason="needs a local TEST_DATABASE_URL",
)
def test_read_descriptions_on_real_postgres():
    import psycopg

    from nightcrawler.store.migrate import migrate

    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        migrate(conn)
        for t in ("verdicts", "concert_sources", "raw_events", "concerts"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("INSERT INTO concerts (id, data) VALUES ('aaaaaaaaaaa1', '{}')")
        conn.execute(
            "INSERT INTO raw_events (id, source, source_key, payload) VALUES "
            "(1, 's', 'k1', '{\"description\": \"court\"}'), "
            "(2, 's', 'k2', '{\"description\": \"la plus longue\"}')"
        )
        conn.execute(
            "INSERT INTO concert_sources (concert_id, raw_id, rule) VALUES "
            "('aaaaaaaaaaa1', 1, 'new'), ('aaaaaaaaaaa1', 2, 'new')"
        )
    got = content.read_descriptions(url, ["aaaaaaaaaaa1", "zzzzzzzzzzz9"])
    assert got == {"aaaaaaaaaaa1": "la plus longue"}
