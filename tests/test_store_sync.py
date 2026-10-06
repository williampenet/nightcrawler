import contextlib
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


def test_match_reuses_a_renamed_title_with_a_shared_performer():
    old = concert("Sunn O))) - Life Metal Tour", performers=["Sunn O)))"])
    new = concert(
        "Sunn O))) + Boris", "2026-10-10T21:00:00+02:00", performers=["Sunn O)))", "Boris"]
    )
    assert new.id != old.id
    assert match([new], [stored(old)], VENUES) == {0: (old.id, [], "dedup")}


# Review of PR #40: one stored + one new listing of a series must not share an id.
@pytest.mark.parametrize(
    "old, new",
    [
        (("Nuits Sonores: Boris", ()), ("Nuits Sonores: Earth", ())),
        (("Nuits Sonores: Boris", ("Boris",)), ("Nuits Sonores: Earth", ("Earth",))),
        (("Fête de la Musique Lyon : Boris", ()), ("Fête de la Musique Lyon : Earth", ())),
        (("Jazz à Vienne Off - Quartet Truc", ()), ("Jazz à Vienne Off - Trio Machin", ())),
    ],
)
def test_match_refuses_different_acts_of_a_series(old, new):
    a, b = concert(old[0], performers=old[1]), concert(new[0], performers=new[1])
    assert match([b], [stored(a)], VENUES) == {}


def test_match_refuses_a_series_night_an_hour_later():
    a = concert("Bulbe Sessions #12 : Boris")
    b = concert("Bulbe Sessions #12 : Earth", start="2026-10-10T21:00:00+02:00")
    assert match([b], [stored(a)], VENUES) == {}


def test_match_refuses_other_days_and_skips_unusable_stored_rows():
    old = concert("Drone Night")
    other_day = concert("Drone Night", start="2026-10-11T20:00:00+02:00")
    broken = [("x", [], {"title": "no start"}), ("y", [], {**old.to_dict(), "start": "?"})]
    assert match([other_day], [stored(old), *broken], VENUES) == {}


def test_exact_id_wins_over_an_alias():
    a, b = concert("Drone Night"), concert("Kraut session")
    a.aliases = [b.id]  # a lists b's id as an alias; b is stored under its own id
    found = match([a, b], [stored(b), stored(a)], VENUES)
    assert found == {0: (a.id, [b.id], "id"), 1: (b.id, [], "id")}


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


class FakeConn:
    """Connects; every statement after the encoding setting raises `error`."""

    def __init__(self, encoding="utf-8", error=None):
        self.info = type("Info", (), {"encoding": encoding})()
        self.error, self.calls = error, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return contextlib.nullcontext()

    def execute(self, sql, params=None):
        self.calls.append(sql)
        if self.error and not sql.startswith("SET client_encoding"):
            raise self.error


def _sync_with(conn, tz):
    cs = [concert("Drone Night")]
    out, report, _ = sync(
        "postgresql://x", [], cs, VENUES, NOW, 60, tz, connect=lambda u, **k: conn
    )
    return out is cs, report


def test_statement_timeout_keeps_the_run_result(tz):
    import psycopg

    conn = FakeConn(error=psycopg.errors.QueryCanceled("canceling statement due to timeout"))
    assert _sync_with(conn, tz) == (True, {"status": "error: QueryCanceled"})
    assert conn.calls[-1] == "SET LOCAL statement_timeout = '60s'"


def test_unexpected_encoding_is_refused(tz):
    assert _sync_with(FakeConn(encoding="ascii"), tz) == (True, {"status": "error: encoding"})


def test_store_url_without_secrets(monkeypatch):
    from nightcrawler import cli

    for n in (*cli.STORE_SECRETS, "DATABASE_URL"):
        monkeypatch.delenv(n, raising=False)
    assert cli.store_url() is None
    assert cli.store_command() == 0  # a notice, not a failure


@pytest.mark.skipif(not _local_test_db(), reason="needs a local TEST_DATABASE_URL")
def test_sync_on_real_postgres(tz):
    import psycopg

    from nightcrawler.store.migrate import migrate

    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        migrate(conn)

    def run(title, start, performers=("Sunn O)))",)):
        ev = RawEvent(title, datetime.fromisoformat(start), "json-ld", "v1", url="https://b.ex/1")
        ev.performers = list(performers)
        c = concert(title, start=start, url="https://b.ex/1", performers=performers)
        return sync(url, [ev, ev], [c], VENUES, NOW, 60, tz)  # listed twice: counted once

    out1, rep1, _ = run("Drone Night", "2026-10-10T20:00:00+02:00")
    assert rep1 | {"status": "ok"} == rep1 and rep1["concerts_new"] == 1
    assert rep1["raw_upserted"] == 1
    first = out1[0].id
    out2, rep2, _ = run("Drone Night", "2026-10-10T20:00:00+02:00")
    assert out2[0].id == first and rep2["concerts_reused"] == 1 and rep2["concerts_new"] == 0
    # renamed and moved by an hour, same performer: the id stays, the new id is an alias
    out3, rep3, _ = run("Sunn O))) live", "2026-10-10T21:00:00+02:00")
    renamed = concert("Sunn O))) live", "2026-10-10T21:00:00+02:00", performers=["Sunn O)))"]).id
    assert out3[0].id == first and renamed in out3[0].aliases
    with psycopg.connect(url, autocommit=True, client_encoding="utf8") as conn:
        assert conn.execute("SELECT count(*) FROM concerts").fetchone()[0] == 1
        # one row per raw event, with the rule that first attached it (re-runs keep it)
        rules = {r[0] for r in conn.execute("SELECT rule FROM concert_sources")}
        assert rules == {"new", "dedup"}
        conn.execute(
            "INSERT INTO overrides (kind, payload) VALUES ('not_concert', %s)",
            (psycopg.types.json.Jsonb({"concert_id": renamed}),),  # an alias is enough
        )
        conn.execute("INSERT INTO feedback (artist_key, kind) VALUES ('sunn', 'wrong')")
    out4, rep4, reported = run("Sunn O))) live", "2026-10-10T21:00:00+02:00")
    assert out4 == [] and rep4["overrides_applied"] == 1 and reported == {"sunn"}
