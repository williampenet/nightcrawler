"""judge_taste eval runner (WIP-57): cases, metrics, aggregates only. Offline, fake model."""

import json
import shutil

import pytest

import eval.judge.__main__ as runner
from nightcrawler import llm

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

LIKED, DISLIKED, WATCHED, REFONLY = "aaaaaaaaaaa1", "ddddddddddd1", "ccccccccccc1", "eeeeeeeeeee1"
CONCERTS = [
    {"id": LIKED, "title": "Earth live", "venue_name": "Le Sonic",
     "start": "2026-10-10T20:00:00+02:00", "performers": ["Earth"], "artists": ["earth"]},
    {"id": DISLIKED, "title": "Popstar tour", "venue_name": "Transbordeur",
     "start": "2026-10-11T20:00:00+02:00", "performers": ["Popstar"], "artists": ["popstar"]},
    {"id": WATCHED, "title": "Boris night", "venue_name": "Le Périscope",
     "start": "2026-10-12T20:00:00+02:00", "performers": ["Boris"], "artists": []},
    {"id": REFONLY, "title": "Sunn O))) mass", "venue_name": "Grrrnd Zero",
     "start": "2026-10-13T20:00:00+02:00", "performers": ["Sunn O)))"], "artists": []},
]  # fmt: skip
REFERENCE = [
    {"date": "2026-07-26", "artists": "Old Band + Other Band", "venue": "Rock n Eat, Lyon 9e"},
    {"date": "2026-10-12", "artists": "Boris", "venue": "Le Périscope"},
    {"date": "2026-10-13", "artists": "Sunn O)))", "venue": "Grrrnd Zero"},
]
COVERAGE = {"events": [{"row": 1, "found": True, "concert_id": WATCHED},
                       {"row": 2, "found": True, "concert_id": REFONLY}]}  # fmt: skip
LABELS = [
    {"id": LIKED, "label": "liked", "rule": 0.0},
    {"id": DISLIKED, "label": "disliked", "rule": 0.9},
    {"id": WATCHED, "label": "disliked", "rule": 0.0},
]
PRIVATE = ["Earth", "Popstar", "Boris", "Sunn", "Old Band", "goût secret", LIKED, DISLIKED]


def cases():
    return runner.build_cases(LABELS, CONCERTS, REFERENCE, COVERAGE)


def test_build_cases_kinds_and_watch():
    cs = cases()
    kinds = [(c["concert"]["id"], c["kind"], c["label"]) for c in cs]
    assert kinds == [
        (LIKED, "rated", "liked"),
        (DISLIKED, "rated", "disliked"),
        (WATCHED, "rated", "disliked"),  # rated and reported: William's label wins
        (REFONLY, "ref_matched", "positive"),
        ("ref0", "ref_row", "positive"),  # rows 1 and 2 are not judged a second time
    ]
    assert [c.get("watch") for c in cs[:3]] == [False, False, True]
    row = cs[4]["concert"]
    assert row["lineup"] == ["Old Band", "Other Band"] and row["start"] == "2026-07-26"


def test_references_rule_and_watch():
    ref = runner.references(cases())
    assert ref["labels"] == 3 and ref["liked"] == 1 and ref["disliked"] == 2
    assert ref["positives"] == 3  # 1 liked + 1 matched + 1 row
    assert ref["rule_pairwise"] == 0.25  # liked 0.0 vs disliked 0.9 (loss) and 0.0 (tie)
    assert (ref["watch_rated"], ref["watch_liked"], ref["watch_precision"]) == (1, 0, 0.0)


def ans(verdict, conf=50, latency=1.0):
    data = {"verdict": verdict, "reason": "r", "confidence": conf} if verdict else None
    return {"data": data, "latency": latency, "tin": 2000, "tout": 50,
            "error": None if verdict else "schema"}  # fmt: skip


def test_metrics():
    cand = {"price_eur_per_mtok": {"in": 1.0, "out": 10.0}}
    answers = [ans("for_you", 80), ans("must_see"), ans("no"), ans("discovery"), ans(None)]
    m = runner.metrics(cand, cases(), answers)
    assert m["valid"] == 0.8 and m["errors"] == {"schema": 1}
    assert m["recall"] == round(1 / 3, 3)  # liked picked; matched = discovery; row invalid
    assert m["recall_with_discovery"] == round(2 / 3, 3)
    assert m["recall_by_kind"] == {"rated": [1, 1], "ref_matched": [0, 1], "ref_row": [0, 1]}
    assert m["picked_rated"] == 2 and m["precision"] == 0.5  # liked + one disliked picked
    assert m["pairwise"] == 0.5  # liked for_you 80 < disliked must_see; > disliked "no"
    assert m["eur_per_1000"] == 2.5  # (2000 * 1 + 50 * 10) / 1000
    assert m["verdicts"] == {"must_see": 1, "for_you": 1, "discovery": 1, "no": 1, None: 1}


def test_judge_one_maps_errors(monkeypatch):
    spec = llm.ModelSpec(provider="scaleway", model="m")

    def bad(*a, **k):
        raise llm.ModelError("m: HTTP 400")

    monkeypatch.setattr(llm, "chat_json", bad)
    assert runner.judge_one(spec, [], None)["error"] == "transport"
    out = llm.Answer({"verdict": "no", "reason": "r", "confidence": 300}, [], "m", 1.0, 10, 5)
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: out)
    r = runner.judge_one(spec, [], None)
    assert r["data"] is None and r["error"] == "check"


@needs_node
def test_labels_js_uses_the_taste_eval_labels():
    profile = {"seeds": [], "liked": ["earth"], "disliked": ["popstar"], "hidden": [DISLIKED]}
    payload = {"state": profile, "feedback": [], "concerts": CONCERTS[:2], "artists": {}}
    got = {x["id"]: x["label"] for x in runner.run_labels(payload)}
    assert got == {DISLIKED: "disliked"}  # artist-only like is not a label (TASTE_EVAL.md)


def test_main_publishes_aggregates_only(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("SCW_GENAI_SECRET_KEY", "k")
    monkeypatch.delenv("SCW_DEFAULT_PROJECT_ID", raising=False)
    monkeypatch.setattr(runner.taste, "database_url", lambda: "postgresql://u:p@h/db")
    monkeypatch.setattr(
        runner.taste,
        "load_store",
        lambda url: {
            "state": {"taste_text": "goût secret", "seeds": [{"name": "Earth"}]},
            "feedback": [],
        },
    )
    monkeypatch.setattr(
        runner.taste,
        "load_site",
        lambda s: {
            "concerts": CONCERTS,
            "artists": {},
            "generated_at": "2026-10-08T06:00:00+02:00",
            "coverage": COVERAGE,
        },
    )
    monkeypatch.setattr(runner, "run_labels", lambda payload: LABELS)
    monkeypatch.setattr(runner, "load_reference", lambda path: REFERENCE)
    prompts = []

    def fake(spec, messages, schema, **kw):
        prompts.append(messages[1]["content"])
        return llm.Answer(
            {"verdict": "for_you", "reason": "Earth", "confidence": 60},
            [],
            spec.model,
            0.5,
            100,
            10,
        )

    monkeypatch.setattr(llm, "chat_json", fake)
    out = tmp_path / "r.json"
    rc = runner.main(["--only", "mistral-small-3.2-scaleway", "--workers", "1",
                      "--results", str(out)])  # fmt: skip
    assert rc == 0
    assert len(prompts) == 2 * 5  # two conditions x five cases
    assert sum("Concerts qu'elle a aimés" in p for p in prompts) > 0  # examples condition
    assert all("goût secret" in p for p in prompts)
    printed = capsys.readouterr().out + out.read_text()
    for s in PRIVATE:
        assert s not in printed
    data = json.loads(out.read_text())
    assert set(data["results"]) == {"mistral-small-3.2-scaleway [profile]",
                                    "mistral-small-3.2-scaleway [profile+examples]"}  # fmt: skip


def test_main_skips_without_store(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.taste, "database_url", lambda: None)
    assert runner.main(["--results", str(tmp_path / "r.json")]) == 0  # skipped, nothing called
