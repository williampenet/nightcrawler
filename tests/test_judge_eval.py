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
PRIVATE = ["Earth", "Popstar", "Boris", "Sunn", "Old Band", "goût secret", "Sonic", "Périscope",
           "Transbordeur", "Grrrnd", LIKED, DISLIKED, WATCHED, REFONLY]  # fmt: skip


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


def test_build_cases_second_row_on_a_rated_concert_and_stale_rows():
    ref = [*REFERENCE, {"date": "2026-10-12", "artists": "Boris + guest", "venue": "Le Périscope"}]
    cov = {"events": [*COVERAGE["events"], {"row": 3, "found": True, "concert_id": WATCHED},
                      {"row": 0, "found": True, "concert_id": LIKED}]}  # fmt: skip
    cs = runner.build_cases(LABELS, CONCERTS, ref, cov)
    # row 3 matched the disliked concert too: never judged again as a positive; row 0's date
    # differs from LIKED's (CSV edited since the report): ignored, row 0 judged from its text
    assert [c["concert"]["id"] for c in cs if c["kind"] == "ref_row"] == ["ref0"]
    assert [c["watch"] for c in cs if c["kind"] == "rated"] == [False, False, True]


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
    assert m["pairwise_valid"] == 0.5
    assert m["recall"] == round(1 / 3, 3)  # liked picked; matched = discovery; row invalid
    assert m["recall_with_discovery"] == round(2 / 3, 3)
    assert m["recall_by_kind"] == {"rated": [1, 1], "ref_matched": [0, 1], "ref_row": [0, 1]}
    assert m["picked_rated"] == 2 and m["precision"] == 0.5  # liked + one disliked picked
    assert m["pairwise"] == 0.5  # liked for_you 80 < disliked must_see; > disliked "no"
    assert m["eur_per_1000"] == 2.5  # (2000 * 1 + 50 * 10) / 1000
    assert m["verdicts"] == {"must_see": 1, "for_you": 1, "discovery": 1, "no": 1, None: 1}


def test_metrics_without_a_valid_answer_reports_no_quality():
    m = runner.metrics({}, cases(), [ans(None)] * 5)
    assert m["valid"] == 0.0 and m["pairwise"] is None and m["recall"] is None
    assert runner.wilson(0, 21)[0] == 0.0 and runner.wilson(21, 21)[1] == 1.0


def test_judge_one_maps_errors(monkeypatch):
    spec = llm.ModelSpec(provider="scaleway", model="m")

    def bad(*a, **k):
        raise llm.ModelError("m: HTTP 400")

    monkeypatch.setattr(llm, "chat_json", bad)
    assert runner.judge_one(spec, [], None)["error"] == "http_400"
    assert runner.error_code(llm.ModelError("m: ReadTimeout")) == "timeout"
    assert runner.error_code(llm.ModelError("m: ConnectError")) == "transport"
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


TEST_MODELS = """judge_taste:
  - {id: mistral-small-3.2-scaleway, provider: scaleway, model: mistral-small-3.2-24b-instruct-2506,
     price_eur_per_mtok: {in: 0.15, out: 0.35}}
  - {id: gemma-4-26b-a4b-scaleway, provider: scaleway, model: gemma-4-26b-a4b-it,
     price_eur_per_mtok: {in: 0.25, out: 0.50}}
  - {id: retired-one, provider: scaleway, model: x, retired: true}
"""
OLD_CONDITIONS = ["--conditions", "profile,profile+examples"]
BOTH = "mistral-small-3.2-scaleway,gemma-4-26b-a4b-scaleway"


def _setup(monkeypatch):
    import tempfile
    from pathlib import Path

    root = Path(tempfile.mkdtemp())
    (root / "models.yaml").write_text(TEST_MODELS)
    monkeypatch.setattr(runner, "ROOT", root)  # only candidates() reads ROOT at run time
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
    monkeypatch.setattr(runner.taste, "load_site", lambda s: {
        "concerts": CONCERTS, "artists": {}, "generated_at": "2026-10-08T06:00:00+02:00",
        "coverage": COVERAGE})  # fmt: skip
    monkeypatch.setattr(runner, "run_labels", lambda payload: LABELS)
    monkeypatch.setattr(runner, "load_reference", lambda path: REFERENCE)
    monkeypatch.setattr(runner, "load_descriptions", lambda url, ids: {})


def test_main_publishes_aggregates_only(monkeypatch, tmp_path, capsys, caplog):
    _setup(monkeypatch)
    caplog.set_level("DEBUG")
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
    rc = runner.main([*OLD_CONDITIONS, "--only", "mistral-small-3.2-scaleway", "--workers", "1",
                      "--results", str(out)])  # fmt: skip
    assert rc == 0
    assert len(prompts) == 2 * 5  # two conditions x five cases
    assert sum("Concerts qu'elle a aimés" in p for p in prompts) > 0  # examples condition
    assert all("goût secret" in p for p in prompts)
    cap = capsys.readouterr()
    printed = cap.out + cap.err + caplog.text + out.read_text()
    for s in PRIVATE:
        assert s not in printed
    data = json.loads(out.read_text())
    assert set(data["results"]) == {"mistral-small-3.2-scaleway [profile]",
                                    "mistral-small-3.2-scaleway [profile+examples]"}  # fmt: skip


def test_main_skips_without_store(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.taste, "database_url", lambda: None)
    assert runner.main(["--results", str(tmp_path / "r.json")]) == 0  # skipped, nothing called


def test_main_failing_candidate_stops_early_and_fails_the_run(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch)
    calls = []

    def http_400(spec, messages, schema, **kw):
        calls.append(1)
        raise llm.ModelError(f"{spec.model}: HTTP 400")

    monkeypatch.setattr(llm, "chat_json", http_400)
    monkeypatch.setattr(runner, "STOP_AFTER", 2)
    out = tmp_path / "r.json"
    rc = runner.main(["--only", "gemma-4-26b-a4b-scaleway", "--workers", "1", "--conditions",
                      "profile", "--results", str(out)])  # fmt: skip
    assert rc == 1 and len(calls) == 2  # stopped after 2 failed calls in a row
    m = json.loads(out.read_text())["results"]["gemma-4-26b-a4b-scaleway [profile]"]
    assert m["errors"] == {"http_400": 2, "aborted": 3} and m["pairwise"] is None
    assert "::warning::" in capsys.readouterr().out


def test_main_isolates_an_unexpected_error(monkeypatch, tmp_path):
    _setup(monkeypatch)
    monkeypatch.setattr(runner, "metrics", lambda *a: 1 / 0)
    monkeypatch.setattr(
        llm,
        "chat_json",
        lambda *a, **k: llm.Answer(
            {"verdict": "no", "reason": "r", "confidence": 50}, [], "m", 0.1, 1, 1
        ),
    )
    out = tmp_path / "r.json"
    rc = runner.main([*OLD_CONDITIONS, "--only", "mistral-small-3.2-scaleway", "--workers", "1",
                      "--results", str(out)])  # fmt: skip
    skipped = json.loads(out.read_text())["skipped"]
    assert skipped == {
        "mistral-small-3.2-scaleway [profile]": "error: ZeroDivisionError",
        "mistral-small-3.2-scaleway [profile+examples]": "error: ZeroDivisionError",
    }
    assert rc == 1


def test_main_cost_guard(monkeypatch, tmp_path):
    _setup(monkeypatch)
    monkeypatch.setattr(
        llm,
        "chat_json",
        lambda *a, **k: llm.Answer(
            {"verdict": "no", "reason": "r", "confidence": 50}, [], "m", 0.1, None, None
        ),
    )
    out = tmp_path / "r.json"
    rc = runner.main([*OLD_CONDITIONS, "--only", BOTH,
                      "--workers", "1", "--max-calls", "6", "--results", str(out)])  # fmt: skip
    data = json.loads(out.read_text())
    # null token counts read as 0; every "no": precision 0 (ADR-0006); then the cost guard
    m = data["results"]["mistral-small-3.2-scaleway [profile]"]
    assert m["tokens_in"] == 0 and m["precision"] == 0.0 and m["picked_rated"] == 0
    assert "cost guard" in data["skipped"]["gemma-4-26b-a4b-scaleway [profile]"]
    assert rc == 0


def test_candidates_are_eu_only(monkeypatch, tmp_path):
    bad = tmp_path / "models.yaml"
    bad.write_text("judge_taste:\n  - {id: x, provider: mistral, model: m}\n")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="not allowed for personal data"):
        runner.candidates("")


def test_annotations_fit_the_per_step_cap(monkeypatch, tmp_path, capsys):
    """One annotation per active candidate plus the opening one, references included (WIP-76);
    the three larger models are retired since run 2 (WIP-79)."""
    _setup(monkeypatch)
    monkeypatch.setattr(
        llm,
        "chat_json",
        lambda spec, *a, **k: llm.Answer(
            {"verdict": "for_you", "reason": "r", "confidence": 60}, [], spec.model, 0.1, 1, 1
        ),
    )
    assert (
        runner.main([*OLD_CONDITIONS, "--workers", "1", "--results", str(tmp_path / "r.json")]) == 0
    )
    out = capsys.readouterr().out
    notes = [ln for ln in out.splitlines() if ln.startswith(("::notice::", "::warning::"))]
    assert len(notes) == 1 + 2 and "rule-based pairwise 25%" in notes[0]
    assert all("[profile]" in n and "[profile+examples]" in n for n in notes[1:])


def test_failed_pair_and_partial_validity_stay_in_one_annotation(monkeypatch, tmp_path, capsys):
    """A failed pair is reported in its candidate's annotation, which becomes an error and stays
    one even when a later pair is only partly valid; a partly valid pair alone gives a warning
    (WIP-77). Total: 1 + one per candidate."""
    _setup(monkeypatch)

    def flaky(spec, messages, *a, **k):  # every candidate: the row-text case is invalid
        bad = "Old Band" in messages[1]["content"]
        data = None if bad else {"verdict": "no", "reason": "r", "confidence": 50}
        return llm.Answer(data, [], spec.model, 0.1, 1, 1, reason="schema" if bad else None)

    monkeypatch.setattr(llm, "chat_json", flaky)
    real, seen = runner.metrics, []

    def metrics(cand, *a):  # gemma's first pair raises, its second is partly valid
        seen.append(cand["id"])
        if cand["id"].startswith("gemma") and seen.count(cand["id"]) == 1:
            raise KeyError()
        return real(cand, *a)

    monkeypatch.setattr(runner, "metrics", metrics)
    rc = runner.main([*OLD_CONDITIONS, "--only", BOTH,
                      "--workers", "1", "--results", str(tmp_path / "r.json")])  # fmt: skip
    out = [
        ln
        for ln in capsys.readouterr().out.splitlines()
        if ln.startswith(("::notice::", "::warning::", "::error::"))
    ]
    assert rc == 1 and len(out) == 3
    assert out[1].startswith("::warning::judge_taste mistral-small")  # partly valid only
    gemma = out[2]
    assert gemma.startswith("::error::judge_taste gemma-4-26b-a4b-scaleway [profile]: failed")
    assert "[profile+examples]: recall" in gemma  # the later partly valid pair, still error


def test_empty_written_taste_skips_every_call(monkeypatch, tmp_path, capsys):
    """No paid call without the main FR-5 input, unless forced (WIP-78)."""
    _setup(monkeypatch)
    monkeypatch.setattr(runner.taste, "load_store", lambda url: {"state": {"taste_text": ""},
                                                                 "feedback": []})  # fmt: skip
    calls = []
    monkeypatch.setattr(
        llm,
        "chat_json",
        lambda spec, *a, **k: (
            calls.append(1)
            or llm.Answer(
                {"verdict": "no", "reason": "r", "confidence": 50}, [], spec.model, 0.1, 1, 1
            )
        ),
    )
    out = tmp_path / "r.json"
    assert runner.main(["--workers", "1", "--results", str(out)]) == 0
    assert calls == [] and not out.exists()
    assert "skipped: the written taste is empty" in capsys.readouterr().out
    args = ["--only", "mistral-small-3.2-scaleway", "--conditions", "profile", "--workers", "1"]
    assert runner.main([*args, "--allow-empty-taste", "--results", str(out)]) == 0
    assert len(calls) == 5


def test_descriptions_reach_the_prompt_and_are_counted(monkeypatch, tmp_path, capsys):
    """The listing's own description goes into the concert block (WIP-79); only its count is
    printed."""
    _setup(monkeypatch)
    monkeypatch.setattr(runner, "load_descriptions", lambda url, ids: {
        LIKED: "Drone &amp; doom from Seattle", REFONLY: "Secret blurb"})  # fmt: skip
    prompts = []

    def fake(spec, messages, *a, **k):
        prompts.append(messages[1]["content"])
        return llm.Answer({"verdict": "no", "reason": "r", "confidence": 50}, [], spec.model,
                          0.1, 1, 1)  # fmt: skip

    monkeypatch.setattr(llm, "chat_json", fake)
    args = ["--only", "mistral-small-3.2-scaleway", "--conditions", "profile", "--workers", "1"]
    assert runner.main([*args, "--results", str(tmp_path / "r.json")]) == 0
    assert sum("Présentation par la salle : Drone & doom from Seattle" in p for p in prompts) == 1
    out = capsys.readouterr().out
    assert "2 with the listing's description" in out and "Secret blurb" not in out


def test_load_descriptions_keeps_the_longest_read_only():
    executed = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def transaction(self):
            return self

        def execute(self, sql, params=None):
            executed.append((sql, params))

            class R:
                def fetchall(_):
                    return [("a", "short"), ("a", "the longer one"), ("b", "x")]

            return R()

    got = runner.load_descriptions("postgresql://x", ["a", "b"], connect=lambda *a, **k: Conn())
    assert got == {"a": "the longer one", "b": "x"}
    assert executed[0][0] == "SET TRANSACTION READ ONLY" and "statement_timeout" in executed[1][0]
    sql, params = executed[2]
    assert "ANY(%s)" in sql and "ORDER BY" in sql and params == (["a", "b"],)


def test_subsample_is_nested_per_label_and_seeded():
    rated = [({"id": f"l{i}"}, "liked") for i in range(9)] + [
        ({"id": f"d{i}"}, "disliked") for i in range(6)
    ]
    third, two = runner.subsample(rated, 1 / 3, 1), runner.subsample(rated, 2 / 3, 1)
    assert [lab for _, lab in third].count("liked") == 3 and len(third) == 5
    assert {c["id"] for c, _ in third} <= {c["id"] for c, _ in two}
    assert runner.subsample(rated, 1 / 3, 2) != third and runner.subsample(rated, 1.0, 1) == rated


def _cv_cases(n=60):
    return [{"concert": {"id": f"c{i}"}, "kind": "rated",
             "label": "liked" if i % 3 else "disliked"} for i in range(n)]  # fmt: skip


def test_cv_operating_point_uses_the_other_half():
    """The cut-off is learnt on one half of the cases and applied to the other (WIP-81)."""
    cs = _cv_cases()
    S = runner.judge.score
    yes, no = S({"verdict": "must_see", "confidence": 90}), S({"verdict": "no", "confidence": 80})
    sc = [yes if c["label"] == "liked" else no for c in cs]  # a perfect judge
    p = runner.cv_operating_point(cs, sc, 0.8)
    assert (p["recall"], p["precision"], p["picked_rated"]) == (1.0, 1.0, 40)
    assert p["cuts"] == ["must_see ≥ 90", "must_see ≥ 90"]
    # liked scores differ between halves: each cut-off comes from the *other* half
    fold = [runner._h("fold", c["concert"]["id"]) % 2 for c in cs]
    mid = S({"verdict": "for_you", "confidence": 50})
    sc2 = [(yes if fold[i] == 0 else mid) if c["label"] == "liked" else no
           for i, c in enumerate(cs)]  # fmt: skip
    q = runner.cv_operating_point(cs, sc2, 0.8)
    # half 0 is cut at half 1's level (for_you ≥ 50), half 1 at half 0's (must_see ≥ 90):
    # half 1's likes (for_you 50) fall under it, so recall is only half 0's share
    assert sorted(q["cuts"]) == ["for_you ≥ 50", "must_see ≥ 90"]
    assert q["recall"] == round(sum(1 for i, c in enumerate(cs) if c["label"] == "liked"
                                    and fold[i] == 0) / 40, 3)  # fmt: skip


def test_cv_operating_point_ties_invalid_and_empty_folds():
    cs = _cv_cases()
    tied = [runner.judge.score({"verdict": "discovery", "confidence": 50})] * len(cs)  # all tied
    t = runner.cv_operating_point(cs, tied, 0.8)
    assert t["recall"] == 1.0 and t["precision"] == round(40 / 60, 3)
    assert t["cuts"] == ["discovery ≥ 50", "discovery ≥ 50"]
    invalid = [-1.0 if c["label"] == "liked" and i % 2 else 3.5 for i, c in enumerate(cs)]
    assert runner.cv_operating_point(cs, invalid, 0.9)["recall"] is None  # cut would reach -1
    only_neg = [{**c, "label": "disliked"} for c in cs]
    assert runner.cv_operating_point(only_neg, [1.0] * len(cs), 0.8)["precision"] is None
    S = runner.judge.score
    assert runner.cut_label(S({"verdict": "no", "confidence": 80})) == "no ≤ 80"
    assert runner.cut_label(S({"verdict": "for_you", "confidence": 71})) == "for_you ≥ 71"


def test_learning_curve_never_shows_a_case_its_own_rating(monkeypatch, tmp_path):
    """In every condition, a rated case's prompt holds neither its own rating nor one sharing
    an artist with it (the pool shrinks; the leakage rules do not)."""
    _setup(monkeypatch)
    seen = []

    def fake(spec, messages, *a, **k):
        seen.append(messages[1]["content"])
        return llm.Answer({"verdict": "no", "reason": "r", "confidence": 50}, [], spec.model,
                          0.1, 1, 1)  # fmt: skip

    monkeypatch.setattr(llm, "chat_json", fake)
    for cond in runner.DEFAULT_CONDITIONS:
        seen.clear()
        runner.main(["--only", "mistral-small-3.2-scaleway", "--conditions", cond,
                     "--workers", "1", "--results", str(tmp_path / "r.json")])  # fmt: skip
        if cond in ("nn", "profile+examples"):
            assert any("<<<EXEMPLES" in p for p in seen), cond  # not vacuous
        for prompt in seen:
            target = prompt.split("<<<CONCERT", 1)[1]
            examples = prompt.split("<<<CONCERT", 1)[0]
            for title in ("Earth live", "Popstar tour", "Boris night"):
                if f"Titre : {title}" in target:
                    assert title not in examples, (cond, title)


def test_default_conditions_run_the_learning_curve(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch)
    calls = []

    def fake(spec, messages, *a, **k):
        calls.append(messages[1]["content"])
        return llm.Answer({"verdict": "for_you", "reason": "r", "confidence": 60}, [], spec.model,
                          0.1, 1, 1)  # fmt: skip

    monkeypatch.setattr(llm, "chat_json", fake)
    out = tmp_path / "r.json"
    rc = runner.main(["--only", "mistral-small-3.2-scaleway", "--workers", "1",
                      "--results", str(out)])  # fmt: skip
    assert rc == 0 and len(calls) == 6 * 5
    res = json.loads(out.read_text())["results"]
    assert set(res) == {f"mistral-small-3.2-scaleway [{c}]" for c in runner.DEFAULT_CONDITIONS}
    assert set(res["mistral-small-3.2-scaleway [nn]"]["cv"]) == {"80%", "90%"}
    assert "cut-off cross-validated @80%" in capsys.readouterr().out


def test_shown_metrics_per_section():
    answers = [ans("must_see"), ans("discovery", 40), ans("for_you"), ans("discovery", 10),
               ans(None)]  # fmt: skip
    sh = runner.shown_metrics(cases(), answers)
    # positives: LIKED (must_see, shown), REFONLY (discovery 10: not shown), ref0 (invalid)
    assert sh["recall"] == round(1 / 3, 3)
    assert sh["share_shown"] == 1.0  # the 3 rated concerts are all shown
    secs = sh["sections"]
    assert (secs["ne_pas_rater"]["n"], secs["ne_pas_rater"]["liked"]) == (1, 1)
    assert (secs["decouvertes"]["n"], secs["pour_toi"]["n"], secs["tout_voir"]["n"]) == (1, 1, 0)


def _gate_results(recall):
    return {"mistral-small-3.2-scaleway [nn]": {"shown": {"recall": recall}}}


def test_gate_on_the_routed_model():
    conds = list(runner.DEFAULT_CONDITIONS)
    assert runner.gate(_gate_results(0.9), {}, conds, "") == 0
    assert runner.gate(_gate_results(0.84), {}, conds, "") == 1  # min_quality 0.85
    assert runner.gate({}, {"mistral-small-3.2-scaleway": "no key"}, conds, "") == 1
    assert runner.gate({}, {}, ["profile"], "") == 0  # condition left out: nothing to gate
    assert runner.gate({}, {}, conds, "gemma-4-26b-a4b-scaleway") == 0  # routed left out
