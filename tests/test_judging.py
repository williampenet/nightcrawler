"""Pipeline judging step (ADR-0007, WIP-84): ratings mapping, cache, cap, failures, privacy."""

import contextlib
import json
from datetime import UTC, datetime

import pytest

from nightcrawler import judge, judging, llm
from nightcrawler.models import Concert
from nightcrawler.store import verdicts

SECRET_REASON = "Raison très personnelle sur ton goût"


def concert(cid, start, aliases=(), artists=()):
    return Concert(id=cid, title=f"Show {cid}", start=start, venue_id="v", venue_name="Le Sonic",
                   url=None, ticket_url=None, performers=[], sources=["s"], reason="r",
                   artists=list(artists), aliases=list(aliases))  # fmt: skip


T0 = datetime(2026, 10, 1, tzinfo=UTC)
CONCERTS = [
    concert("aaaaaaaaaaa1", "2026-10-12T20:00:00+02:00", aliases=["aaaaaaaaaaa0"]),
    concert("bbbbbbbbbbb1", "2026-10-10T20:00:00+02:00"),
    concert("ccccccccccc1", "2026-10-11T20:00:00+02:00"),
]


def test_current_ratings_latest_across_id_and_aliases():
    ratings = {
        "aaaaaaaaaaa0": ("liked", T0),  # alias, older
        "aaaaaaaaaaa1": ("disliked", T0.replace(day=2)),  # current id, newer: wins
        "bbbbbbbbbbb1": (None, T0),  # unlike: no rating
        "zzzzzzzzzzz9": ("liked", T0),  # not published: not used
    }
    assert judging.current_ratings(ratings, CONCERTS) == {"aaaaaaaaaaa1": "disliked"}
    newer_alias = ratings | {"aaaaaaaaaaa0": ("liked", T0.replace(day=3))}
    assert judging.current_ratings(newer_alias, CONCERTS) == {"aaaaaaaaaaa1": "liked"}


@pytest.fixture
def task(monkeypatch):
    monkeypatch.setenv("SCW_GENAI_SECRET_KEY", "k")
    monkeypatch.delenv("SCW_DEFAULT_PROJECT_ID", raising=False)
    return llm.load_tasks("config/models.yaml")["judge_taste"]


def setup(monkeypatch, inputs, saved):
    monkeypatch.setattr(verdicts, "load_inputs", lambda conn, ids: inputs)
    monkeypatch.setattr(verdicts, "save", lambda conn, rows: saved.setdefault("rows", rows))
    monkeypatch.setattr(verdicts, "update_sections",
                        lambda conn, ch: saved.setdefault("refresh", ch))  # fmt: skip
    return lambda url: contextlib.nullcontext(object())


def answer(verdict="for_you", conf=70, data=True, errors=()):
    d = {"verdict": verdict, "reason": SECRET_REASON, "confidence": conf} if data else None
    return llm.Answer(d, list(errors), "m", 0.5, 2000, 40, reason=None if data else "schema")


def test_run_judges_changed_inputs_and_keeps_cached_ones(monkeypatch, task):
    inputs = verdicts.Inputs(profile={"taste_text": "Funk, soul."},
                             ratings={"bbbbbbbbbbb1": ("liked", T0)})  # fmt: skip
    saved: dict = {}
    connect = setup(monkeypatch, inputs, saved)
    calls = []

    def fake(t, messages, schema, check):
        calls.append(messages)
        return answer("discovery", 25)

    # first run: nothing stored, all three judged
    rep = judging.run("db", CONCERTS, {}, judging.Context(task, 600), connect, fake)
    assert rep["status"] == "ok" and rep["judged"] == 3 and rep["cached"] == 0
    assert rep["ratings_used"] == {"liked": 1}
    rows = saved["rows"]
    assert [r["concert_id"] for r in rows] == ["bbbbbbbbbbb1", "ccccccccccc1", "aaaaaaaaaaa1"]
    assert rows[0]["section"] == "tout_voir"  # discovery under 30
    # the rated concert is not its own example
    assert "Show bbbbbbbbbbb1" not in calls[0][1]["content"].split("<<<CONCERT")[0]
    # second run: same inputs -> cached; stored section from an older rule is refreshed
    inputs.stored = {r["concert_id"]: r | {"section": "decouvertes"} for r in rows}
    saved.clear()
    calls.clear()
    rep = judging.run("db", CONCERTS, {}, judging.Context(task, 600), connect, fake)
    assert rep["judged"] == 0 and rep["cached"] == 3 and calls == []
    ids = sorted(r["concert_id"] for r in rows)
    assert sorted(saved["refresh"]) == [(c, "tout_voir") for c in ids]
    # privacy: counts only in the report, nothing on the published concerts
    published = json.dumps([c.to_dict() for c in CONCERTS]) + json.dumps(rep)
    assert SECRET_REASON not in published and "verdict" not in published


def test_run_cap_failures_and_stop(monkeypatch, task):
    inputs = verdicts.Inputs(profile={"taste_text": "Funk"})
    saved: dict = {}
    connect = setup(monkeypatch, inputs, saved)
    rep = judging.run("db", CONCERTS, {}, judging.Context(task, 2), connect,
                      lambda *a: answer())  # fmt: skip
    assert rep["judged"] == 2 and rep["capped"] == 1  # soonest first: aaaa… (Oct 12) left out
    assert "aaaaaaaaaaa1" not in [r["concert_id"] for r in saved["rows"]]

    def broken(*a):
        raise llm.ModelError("m: HTTP 400")

    monkeypatch.setattr(judging, "STOP_AFTER", 2)
    monkeypatch.setattr(judging, "WORKERS", 1)
    saved.clear()
    rep = judging.run("db", CONCERTS, {}, judging.Context(task, 600), connect, broken)
    assert rep["judged"] == 0 and rep["failed"] == {"transport": 2, "aborted": 1}
    saved.clear()
    bad = judging.run("db", CONCERTS, {}, judging.Context(task, 600), connect,
                      lambda *a: answer(data=False))  # fmt: skip
    assert bad["failed"] == {"schema": 2, "aborted": 1} and saved["rows"] == []
    saved.clear()
    chk = judging.run("db", CONCERTS[:1], {}, judging.Context(task, 600), connect,
                      lambda *a: answer(conf=120, errors=["confidence out of 0-100"]))  # fmt: skip
    assert chk["failed"] == {"check": 1}


def test_run_skips_without_key_taste_or_store(monkeypatch, task):
    saved: dict = {}
    connect = setup(monkeypatch, verdicts.Inputs(profile={"taste_text": "  "}), saved)
    ctx = judging.Context(task, 600)
    assert judging.run("db", CONCERTS, {}, ctx, connect)["status"].startswith("skipped: the")
    assert judging.run("db", CONCERTS, {}, None, connect)["status"].startswith("skipped")
    monkeypatch.delenv("SCW_GENAI_SECRET_KEY")
    assert "no key" in judging.run("db", CONCERTS, {}, ctx, connect)["status"]
    monkeypatch.setenv("SCW_GENAI_SECRET_KEY", "k")

    def down(url):
        raise OSError("host db.example password=x")

    st = judging.run("db", CONCERTS, {}, ctx, down)["status"]
    assert st == "error: store read (OSError)" and "password" not in st


def test_section_matches_the_stored_check():
    """The sections judging writes satisfy migration 003's CHECK (section follows verdict)."""
    allowed = {"must_see": {"ne_pas_rater"}, "for_you": {"pour_toi"},
               "discovery": {"decouvertes", "tout_voir"}, "no": {"tout_voir"}}  # fmt: skip
    for v, ok in allowed.items():
        for conf in (0, 29, 30, 100):
            assert judge.section({"verdict": v, "confidence": conf}) in ok
