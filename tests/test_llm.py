import hashlib
import json

import httpx
import pytest
import respx

from nightcrawler import llm
from nightcrawler.extract import SCHEMA

EVENT = {
    "title": "Buck",
    "date": "2026-10-08",
    "time": None,
    "performers": ["Buck"],
    "is_concert": True,
}


def reply(obj, status=200):
    content = obj if isinstance(obj, str) else json.dumps(obj)
    return httpx.Response(
        status,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        },
    )


def task(fallback=None):
    primary = llm.ModelSpec(provider="local", model="small", base_url="http://llm.test/v1")
    return llm.Task(name="t", primary=primary, fallback=fallback)


def test_validate_schema_subset():
    assert llm.validate({"events": [EVENT]}, SCHEMA) == []
    bad = {**EVENT, "date": "8 oct", "time": 20, "extra": 1}
    errors = llm.validate({"events": [bad]}, SCHEMA)
    assert any("date" in e for e in errors)
    assert any("time" in e for e in errors)
    assert any("unexpected extra" in e for e in errors)
    assert llm.validate({}, SCHEMA) == ["$: missing events"]


@respx.mock
def test_chat_json_sends_schema_and_parses_fenced_output():
    route = respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=reply("```json\n" + json.dumps({"events": [EVENT]}) + "\n```")
    )
    a = llm.chat_json(task().primary, [{"role": "user", "content": "x"}], SCHEMA)
    assert a.data == {"events": [EVENT]} and a.tokens_in == 100
    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"]["json_schema"]["schema"] == SCHEMA
    assert "Authorization" not in route.calls[0].request.headers


@respx.mock
def test_invalid_output_is_reported_not_repaired():
    respx.post("http://llm.test/v1/chat/completions").mock(return_value=reply("not json"))
    a = llm.chat_json(task().primary, [], SCHEMA)
    assert a.data is None and a.errors


@respx.mock
def test_http_error_never_echoes_body():
    respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="prompt: secret page")
    )
    with pytest.raises(llm.ModelError) as exc:
        llm.chat_json(task().primary, [], SCHEMA)
    assert "secret" not in str(exc.value)


@respx.mock
def test_run_task_escalates_once_on_failed_check(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    respx.post("http://llm.test/v1/chat/completions").mock(return_value=reply({"events": []}))
    big = respx.post("https://api.mistral.ai/v1/chat/completions").mock(
        return_value=reply({"events": [EVENT]})
    )
    t = task(llm.ModelSpec(provider="mistral", model="big"))
    a = llm.run_task(t, [], SCHEMA, check=lambda d: [] if d["events"] else ["empty"])
    assert a.escalated and a.data == {"events": [EVENT]}
    assert big.calls[0].request.headers["Authorization"] == "Bearer k"


@respx.mock
def test_run_task_without_key_keeps_primary(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    respx.post("http://llm.test/v1/chat/completions").mock(return_value=reply({"events": []}))
    t = task(llm.ModelSpec(provider="mistral", model="big"))
    a = llm.run_task(t, [], SCHEMA, check=lambda d: ["empty"])
    assert not a.escalated and a.data == {"events": []} and a.errors == ["empty"]


def test_load_tasks_reads_repo_config():
    t = llm.load_tasks("config/models.yaml")["extract_events"]
    assert t.primary.provider == "local" and len(t.primary.sha256) == 64
    assert t.primary.file.endswith(".gguf")


@respx.mock
def test_ensure_weights_rejects_hash_mismatch(tmp_path):
    data = b"GGUF fake weights"
    spec = llm.ModelSpec(
        provider="local",
        model="m",
        repo="org/m",
        revision="abc",
        file="m.gguf",
        sha256=hashlib.sha256(data).hexdigest(),
    )
    url = "https://huggingface.co/org/m/resolve/abc/m.gguf"
    respx.get(url).mock(return_value=httpx.Response(200, content=data))
    path = llm.ensure_weights(spec, tmp_path)
    assert path.read_bytes() == data
    assert llm.ensure_weights(spec, tmp_path) == path  # cached, verified again

    spec.sha256 = "0" * 64
    respx.get(url).mock(return_value=httpx.Response(200, content=b"tampered"))
    with pytest.raises(llm.ModelError, match="SHA-256"):
        llm.ensure_weights(spec, tmp_path)
    spec.file = "m.bin"
    with pytest.raises(llm.ModelError, match="GGUF"):
        llm.ensure_weights(spec, tmp_path)


def test_validate_fails_closed_on_unknown_schema():
    assert llm.validate("x", {"type": "number"})
    assert llm.validate("x", {})


@respx.mock
def test_non_json_response_is_a_model_error():
    respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text="<html>")
    )
    with pytest.raises(llm.ModelError):
        llm.chat_json(task().primary, [], SCHEMA)
