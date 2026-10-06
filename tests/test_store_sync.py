import os
from datetime import datetime

import pytest

from nightcrawler.artists import Artist
from nightcrawler.dedup import concert_id
from nightcrawler.models import Concert, RawEvent, Venue
from nightcrawler.store.sync import mark_reported, match, sync


def _local_test_db() -> bool:
    # the test drops the public schema: never run it against anything but a local server
    from urllib.parse import urlsplit

    url = os.environ.get("TEST_DATABASE_URL", "")
    return bool(url) and urlsplit(url).hostname in ("localhost", "127.0.0.1")


VENUES = {"v1": Venue("v1", "Le Bulbe", 45.76, 4.83, "music_venue")}
NOW = datetime.fromisoformat("2026-10-05T12:00:00+02:00")


def concert(title, start="2026-10-10T20:00:00+02:00", venue="v1", performers=(), url=None):
    c = Concert("", title, start, venue, "Le Bulbe", url, None, list(performers), ["json-ld"], "")
    c.id = concert_id(c)
    c.links = [{"label": "Page", "url": url}] if url else []
    return c


def stored(c):
    return (c.id, list(c.aliases), c.to_dict())


def test_match_reuses_by_id_or_alias():
    old = concert("Drone Night")
    new = concert("Drone Night")
    other = concert("Kraut session")
    other.aliases = ["abc"]
    found = match([new, other], [stored(old), ("abc", [], concert("x").to_dict())], VENUES)
    assert found == {0: (old.id, [], "id"), 1: ("abc", [], "id")}


def test_match_reuses_a_renamed_title_within_the_dedup_rules():
    old = concert("Drone Night")
    new = concert("Drone Night with Sunn", start="2026-10-10T21:00:00+02:00")
    assert new.id != old.id
    assert match([new], [stored(old)], VENUES) == {0: (old.id, [], "dedup")}


def test_match_refuses_other_days_and_ambiguous_candidates():
    old = concert("Drone Night")
    assert (
        match([concert("Drone Night", start="2026-10-11T20:00:00+02:00")], [stored(old)], {}) == {}
    )
    # two stored acts of a festival both look like the new listing: no guess
    a, b = concert("Nuits Sonores: Boris"), concert("Nuits Sonores: Earth")
    assert match([concert("Nuits Sonores: Boris Live")], [stored(a), stored(b)], VENUES) == {}


def test_mark_reported_drops_guesses():
    a = Artist("asna", "Asna", 1, 5000, ["X"], ["rock"], True)
    assert mark_reported({"asna": a, "boris": Artist("boris", "Boris")}, {"asna", "gone"}) == 1
    assert (a.related, a.tags, a.confident, a.doubt) == ([], [], False, "reported")


def test_store_error_keeps_the_run_result(tz):
    import psycopg

    def down(*args, **kwargs):
        raise psycopg.OperationalError("connection to server failed: secret details")

    cs = [concert("Drone Night")]
    out, report, reported = sync("postgresql://x", [], cs, VENUES, NOW, 60, tz, connect=down)
    assert out is cs and reported == set()
    assert report == {"status": "error: OperationalError"}  # type only, never the message


@pytest.mark.skipif(not _local_test_db(), reason="needs a local TEST_DATABASE_URL")
def test_sync_on_real_postgres(tz):
    import psycopg

    from nightcrawler.store.migrate import migrate

    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        migrate(conn)

    def run(title, start):
        ev = RawEvent(title, datetime.fromisoformat(start), "json-ld", "v1", url="https://b.ex/1")
        c = concert(title, start=start, url="https://b.ex/1")
        return sync(url, [ev], [c], VENUES, NOW, 60, tz)

    out1, rep1, _ = run("Drone Night", "2026-10-10T20:00:00+02:00")
    assert rep1 | {"status": "ok"} == rep1 and rep1["concerts_new"] == 1
    assert rep1["raw_upserted"] == 1
    first = out1[0].id
    out2, rep2, _ = run("Drone Night", "2026-10-10T20:00:00+02:00")
    assert out2[0].id == first and rep2["concerts_reused"] == 1 and rep2["concerts_new"] == 0
    # renamed and moved by an hour: the dedup rules keep the id, the new id becomes an alias
    out3, rep3, _ = run("Drone Night with Sunn", "2026-10-10T21:00:00+02:00")
    renamed = concert("Drone Night with Sunn", start="2026-10-10T21:00:00+02:00").id
    assert out3[0].id == first and renamed in out3[0].aliases
    with psycopg.connect(url, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM concerts").fetchone()[0] == 1
        # one row per raw event, with the rule that first attached it (re-runs keep it)
        rules = {r[0] for r in conn.execute("SELECT rule FROM concert_sources")}
        assert rules == {"new", "dedup"}
        conn.execute(
            "INSERT INTO overrides (kind, payload) VALUES ('not_concert', %s)",
            (psycopg.types.json.Jsonb({"concert_id": renamed}),),  # an alias is enough
        )
        conn.execute("INSERT INTO feedback (artist_key, kind) VALUES ('sunn', 'wrong')")
    out4, rep4, reported = run("Drone Night with Sunn", "2026-10-10T21:00:00+02:00")
    assert out4 == [] and rep4["overrides_applied"] == 1 and reported == {"sunn"}
