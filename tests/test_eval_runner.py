"""The eval runner on hosted candidates, offline (respx): skip without key, metrics with one."""

import json
from collections import namedtuple

import httpx
import pytest
import respx
import yaml

import eval.__main__ as runner
from eval.__main__ import ROOT, main

HOSTED = ["mistral-small-3.2-scaleway", "gemma-4-26b-a4b-scaleway", "qwen3.6-35b-a3b-scaleway"]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.delenv("SCW_DEFAULT_PROJECT_ID", raising=False)
    return tmp_path


def run(tmp_path, ids):
    code = main(["--only", ",".join(ids), "--results-dir", str(tmp_path)])
    summary = (tmp_path / "summary.md").read_text("utf-8")
    return code, summary, json.loads((tmp_path / "latest.json").read_text("utf-8"))


def test_candidates_file_pins_scaleway_ids_and_retires_old_mistral():
    cands = {
        c["id"]: c for c in yaml.safe_load((ROOT / "models.yaml").read_text())["extract_events"]
    }
    assert {cands[i]["model"] for i in HOSTED} == {
        "mistral-small-3.2-24b-instruct-2506",
        "gemma-4-26b-a4b-it",
        "qwen3.6-35b-a3b",
    }
    for i in HOSTED:
        assert cands[i]["provider"] == "scaleway" and set(cands[i]["price_eur_per_mtok"]) == {
            "in",
            "out",
        }
    assert cands["qwen3.6-35b-a3b-scaleway"]["extra"] == {"reasoning_effort": "none"}
    assert cands["mistral-small-api"]["retired"] is True
    local = cands["ministral-3-14b-q4"]
    assert local["file"].endswith(".gguf") and len(local["sha256"]) == 64
    assert len(local["revision"]) == 40


def test_hosted_candidates_skipped_without_key(env, monkeypatch, capsys):
    monkeypatch.delenv("SCW_GENAI_SECRET_KEY", raising=False)
    code, summary, results = run(env, HOSTED + ["mistral-small-api"])
    assert code == 1  # the routed Gemma was not measured: the gate fails (see below)
    for i in HOSTED:
        assert results[i] == {"skipped": "no key (SCW_GENAI_SECRET_KEY not set)"}
        assert f"| {i} |" in summary
    assert summary.count("skipped: no key") == 3
    assert results["mistral-small-api"] == {"skipped": "retired"}
    assert "::notice::mistral-small-3.2-scaleway: skipped (no key" in capsys.readouterr().out


@respx.mock
def test_hosted_candidate_metrics_tokens_and_cost(env, monkeypatch, capsys):
    monkeypatch.setenv("SCW_GENAI_SECRET_KEY", "k")
    calls = iter(
        [httpx.Response(429)]
        + [
            httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps({"events": []})}}],
                    "usage": {"prompt_tokens": 2000, "completion_tokens": 400},
                },
            )
        ]
        * 50
    )
    respx.post("https://api.scaleway.ai/v1/chat/completions").mock(
        side_effect=lambda request: next(calls)
    )
    monkeypatch.setattr("time.sleep", lambda s: None)
    code, summary, results = run(env, ["mistral-small-3.2-scaleway"])
    m = results["mistral-small-3.2-scaleway"]["metrics"]
    assert code == 0 and m["valid_rate"] == 1.0 and m["checked"]["injected_events"] == 0
    assert (m["tokens_in_avg"], m["tokens_out_avg"], m["retries"], m["failed_calls"]) == (
        2000,
        400,
        1,
        0,
    )
    # 2,000 in x €0.15/M + 400 out x €0.35/M = €0.00044 per page -> €0.44 per 1,000 pages
    assert m["cost_eur_per_1000"] == 0.44
    assert "| 2000 / 400 | 0 / 1 | €0.44 |" in summary
    out = capsys.readouterr().out
    assert "::notice::mistral-small-3.2-scaleway: F1 " in out and "cost/1000 0.44 EUR" in out


@respx.mock
def test_no_page_answered_shows_cost_na(env, monkeypatch):
    monkeypatch.setenv("SCW_GENAI_SECRET_KEY", "k")
    respx.post("https://api.scaleway.ai/v1/chat/completions").mock(return_value=httpx.Response(503))
    monkeypatch.setattr("time.sleep", lambda s: None)
    _, summary, results = run(env, ["gemma-4-26b-a4b-scaleway"])
    m = results["gemma-4-26b-a4b-scaleway"]["metrics"]
    assert m["cost_eur_per_1000"] is None and m["failed_calls"] == 7 and m["retries"] == 0
    assert summary.rstrip().endswith("| 0 / 0 | 7 / 0 | n/a |")


def test_hosted_run_first_and_one_crash_never_aborts_the_rest(env, monkeypatch, capsys):
    order = []

    def fake(cand, cases, task):
        order.append(cand["id"])
        if cand["provider"] == "local":
            raise OSError("/secret/path: No space left on device")
        return {"skipped": "fake"}

    monkeypatch.setattr(runner, "run_candidate", fake)
    ids = ["ministral-3-14b-q4", "ministral-3-3b-q4", "mistral-small-3.2-scaleway", "nope"]
    code, summary, results = run(env, ids)
    assert code == 0
    assert order == ["mistral-small-3.2-scaleway", "ministral-3-3b-q4", "ministral-3-14b-q4"]
    assert results["ministral-3-14b-q4"] == {"skipped": "error: OSError"}  # no message leaked
    assert "/secret/path" not in summary
    assert "::warning::--only: unknown candidate id(s): nope" in capsys.readouterr().out


def test_local_model_skipped_when_disk_is_low(env, monkeypatch):
    monkeypatch.setenv("LLAMA_SERVER", "/bin/false")
    monkeypatch.setenv("MODEL_SCRATCH", str(env / "scratch"))
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda p: Usage(0, 0, 9 * 10**9))
    fetched = []
    monkeypatch.setattr(runner.llm, "ensure_weights", lambda *a: fetched.append(a))
    cand = {
        c["id"]: c for c in yaml.safe_load((ROOT / "models.yaml").read_text())["extract_events"]
    }
    # 8.24 GB + 2 GiB margin > 9 GB free
    res = runner.run_candidate(cand["ministral-3-14b-q4"], [], None)
    assert res["skipped"].startswith("disk (9.0 GB free < 10.4 GB needed)") and not fetched
    assert runner.low_disk(env, 1000) is None  # 1 kB + margin fits in 9 GB


def test_uncached_weights_are_removed_after_the_run(env, monkeypatch):
    monkeypatch.setenv("LLAMA_SERVER", "/bin/false")
    scratch = env / "scratch"
    monkeypatch.setenv("MODEL_SCRATCH", str(scratch))

    def fake_weights(spec, cache_dir):
        (cache_dir / "w").mkdir(parents=True)
        raise runner.llm.ModelError("download failed")

    monkeypatch.setattr(runner.llm, "ensure_weights", fake_weights)
    cand = {
        c["id"]: c for c in yaml.safe_load((ROOT / "models.yaml").read_text())["extract_events"]
    }
    monkeypatch.setattr(runner, "low_disk", lambda d, s: None)
    with pytest.raises(runner.llm.ModelError):
        runner.run_candidate(cand["ministral-3-14b-q4"], [], None)
    assert not scratch.exists()


@respx.mock
def test_routed_model_below_the_bar_fails_the_eval(env, monkeypatch, capsys):
    """config/models.yaml sets min_quality for extract_events: the routed Gemma is gated."""
    monkeypatch.setenv("SCW_GENAI_SECRET_KEY", "k")
    respx.post("https://api.scaleway.ai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"events": []})}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 10},
            },
        )
    )
    code, _, _ = run(env, ["gemma-4-26b-a4b-scaleway"])
    assert code == 1
    assert "::error::gemma-4-26b-a4b-scaleway: F1 0.0 < 0.85" in capsys.readouterr().out
    # a non-routed candidate with the same answers is reported, never gated
    assert run(env, ["mistral-small-3.2-scaleway"])[0] == 0


@pytest.mark.parametrize(
    "setup, reason",
    [
        (lambda mp: mp.delenv("SCW_GENAI_SECRET_KEY", raising=False), "no key"),
        (
            lambda mp: mp.setattr(
                runner,
                "run_candidate",
                lambda *a: (_ for _ in ()).throw(runner.llm.ModelError("gemma: HTTP 401")),
            ),
            "error: gemma: HTTP 401",
        ),
        (lambda mp: mp.setattr(runner, "run_candidate", lambda *a: 1 / 0), "error: ZeroDivision"),
    ],
    ids=["no-key", "model-error", "crash"],
)
def test_gate_fails_when_routed_model_is_not_measured(env, monkeypatch, capsys, setup, reason):
    setup(monkeypatch)
    code, _, results = run(env, ["routed"])
    assert code == 1 and "skipped" in results["gemma-4-26b-a4b-scaleway"]
    out = capsys.readouterr().out
    assert "::error::gemma-4-26b-a4b-scaleway (routed): not measured (" in out and reason in out


def test_only_routed_selects_the_routed_candidate(env, monkeypatch):
    seen = []
    monkeypatch.setattr(
        runner, "run_candidate", lambda cand, *a: seen.append(cand["id"]) or {"skipped": "fake"}
    )
    run(env, ["routed"])
    assert seen == ["gemma-4-26b-a4b-scaleway"]


def test_gate_fails_when_no_candidate_matches_the_routed_request(env, monkeypatch, capsys):
    real = runner.llm.load_tasks

    def other_extra(path):
        tasks = real(path)
        tasks["extract_events"].primary.extra = {}  # same model id, different request
        return tasks

    monkeypatch.setattr(runner.llm, "load_tasks", other_extra)
    monkeypatch.setattr(runner, "run_candidate", lambda *a: {"skipped": "fake"})
    assert run(env, ["mistral-small-3.2-scaleway"])[0] == 1
    assert "no eval candidate matches the routed model" in capsys.readouterr().out


def test_local_gemma_candidate_is_pinned_to_the_publisher_gguf():
    cands = {
        c["id"]: c for c in yaml.safe_load((ROOT / "models.yaml").read_text())["extract_events"]
    }
    g = cands["gemma-4-26b-a4b-qat-q4-local"]
    assert g["provider"] == "local" and g["repo"].startswith("google/")
    assert g["file"].endswith(".gguf") and len(g["sha256"]) == 64 and len(g["revision"]) == 40
    assert g["size_bytes"] == 14439363584 and g["cache"] is False
