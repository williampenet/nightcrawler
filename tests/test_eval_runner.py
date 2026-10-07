"""The eval runner on hosted candidates, offline (respx): skip without key, metrics with one."""

import json

import httpx
import pytest
import respx
import yaml

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
    assert code == 0
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
