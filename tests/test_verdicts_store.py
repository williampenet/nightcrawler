"""Verdict store (ADR-0007, WIP-83): migration 003, read and write, on a local PostgreSQL when
TEST_DATABASE_URL points to one (CI service); the other tests need no database."""

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest

from nightcrawler import judge, llm
from nightcrawler.store import verdicts

local_db = pytest.mark.skipif(
    urlsplit(os.environ.get("TEST_DATABASE_URL", "")).hostname not in ("localhost", "127.0.0.1"),
    reason="needs a local TEST_DATABASE_URL (the test empties the store tables)",
)
NOW = datetime.now(UTC)


def row(cid, **kw):
    return {"concert_id": cid, "input_hash": "h-" + cid, "verdict": "for_you", "confidence": 70,
            "reason": "Du groove comme tu aimes.", "section": "pour_toi", "model": "m",
            "starts_at": NOW + timedelta(days=3)} | kw  # fmt: skip


@local_db
def test_store_round_trip_on_real_postgres():
    import psycopg
    from psycopg.types.json import Jsonb

    from nightcrawler.store.migrate import migrate

    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        migrate(conn)
        for t in ("verdicts", "concert_sources", "raw_events", "concerts", "feedback", "profile"):
            conn.execute(f"DELETE FROM {t}")
        for cid in ("aaaaaaaaaaa1", "bbbbbbbbbbb1", "ccccccccccc1"):
            conn.execute("INSERT INTO concerts (id, data) VALUES (%s, '{}')", (cid,))
        conn.execute("INSERT INTO profile (data) VALUES (%s)", (Jsonb({"taste_text": "Funk"}),))
        conn.execute(
            "INSERT INTO feedback (concert_id, artist_key, kind, created_at) VALUES "
            "('aaaaaaaaaaa1', 'x', 'like', now() - interval '2 min'), "
            "('aaaaaaaaaaa1', 'y', 'like', now() - interval '2 min'), "
            "('bbbbbbbbbbb1', NULL, 'like', now() - interval '2 min'), "
            "('bbbbbbbbbbb1', NULL, 'unlike', now() - interval '1 min'), "
            "('zzzzzzzzzzz0', NULL, 'dislike', now()), "  # an alias or a past concert
            "(NULL, 'x', 'like', now())"
        )
        conn.execute(
            "INSERT INTO raw_events (id, source, source_key, payload) VALUES "
            "(1, 's', 'k1', '{\"description\": \"court\"}'), "
            "(2, 's', 'k2', '{\"description\": \"la plus longue\"}')"
        )
        conn.execute(
            "INSERT INTO concert_sources (concert_id, raw_id, rule) VALUES "
            "('aaaaaaaaaaa1', 1, 'new'), ('aaaaaaaaaaa1', 2, 'new')"
        )
        assert (
            verdicts.save(
                conn, [row("aaaaaaaaaaa1"), row("ccccccccccc1", starts_at=NOW - timedelta(days=91))]
            )
            == 2
        )
        got = verdicts.load_inputs(conn, ["aaaaaaaaaaa1", "bbbbbbbbbbb1", "ccccccccccc1"])
        assert got.profile == {"taste_text": "Funk"}
        assert got.ratings == {"aaaaaaaaaaa1": "liked", "zzzzzzzzzzz0": "disliked"}
        assert got.descriptions == {"aaaaaaaaaaa1": "la plus longue"}
        assert got.known == {"aaaaaaaaaaa1": "h-aaaaaaaaaaa1"}  # c... purged (started 91 d ago)
        verdicts.save(
            conn, [row("aaaaaaaaaaa1", input_hash="h2", verdict="no", section="tout_voir")]
        )
        v = conn.execute("SELECT input_hash, verdict, section FROM verdicts").fetchall()
        assert v == [("h2", "no", "tout_voir")]
        with pytest.raises(psycopg.errors.CheckViolation):
            verdicts.save(conn, [row("bbbbbbbbbbb1", reason="x" * 241)])


def test_load_inputs_is_read_only_with_a_timeout():
    executed = []

    class Conn:
        def transaction(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            executed.append(sql)

            class R:
                def fetchone(_):
                    return None

                def fetchall(_):
                    return []

            return R()

    got = verdicts.load_inputs(Conn(), ["a"])
    assert executed[:2] == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '30s'"]
    assert got.profile == {} and got.ratings == {} and got.known == {}


def test_round_fans_and_input_hash():
    assert [judge.round_fans(n) for n in (0, 87, 1234, 1251, 987654)] == [0, 87, 1200, 1300,
                                                                            990000]  # fmt: skip
    task = llm.load_tasks("config/models.yaml")["judge_taste"]
    m = judge.messages_for({"id": "x", "title": "T"}, {}, {"taste_text": "Funk"})
    h = judge.input_hash(task, m)
    assert h == judge.input_hash(task, [dict(x) for x in m]) and len(h) == 64
    other = judge.messages_for({"id": "x", "title": "T"}, {}, {"taste_text": "Soul"})
    assert judge.input_hash(task, other) != h  # profile change: re-judged
    task.temperature = 0.5
    assert judge.input_hash(task, m) != h  # settings change: re-judged
