import hashlib
import json
from pathlib import Path

import httpx
import pytest
import respx
import yaml

from nightcrawler import llm
from nightcrawler.extract import SCHEMA, page_text

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
    """ADR-0004: extract_events routed to Gemma 4 26B-A4B on Scaleway, as evaluated in run 3."""
    t = llm.load_tasks("config/models.yaml")["extract_events"]
    assert t.primary.provider == "scaleway" and t.primary.model == "gemma-4-26b-a4b-it"
    assert t.primary.extra == {"reasoning_effort": "none"}
    assert t.fallback is None and t.min_quality == 0.85 and t.temperature == 0.0
    assert t.max_output_tokens == 2048 and t.timeout_s == 600.0
    assert t.max_input_chars == 7000  # not raised without an eval (WIP-66)
    assert t.adr and Path(t.adr).is_file()


def test_routed_model_is_the_evaluated_candidate():
    """The routed request (model + extra) is exactly the eval candidate whose scores are cited."""
    cands = yaml.safe_load(Path("eval/models.yaml").read_text("utf-8"))["extract_events"]
    cand = next(c for c in cands if c["id"] == "gemma-4-26b-a4b-scaleway")
    t = llm.load_tasks("config/models.yaml")["extract_events"]
    assert (t.primary.provider, t.primary.model) == (cand["provider"], cand["model"])
    assert t.primary.extra == cand["extra"]


def _write_task(tmp_path, limits):
    path = tmp_path / "models.yaml"
    path.write_text(
        yaml.safe_dump(
            {"tasks": {"t": {"primary": {"provider": "local", "model": "m"}, "limits": limits}}}
        ),
        "utf-8",
    )
    return path


def test_max_input_chars_defaults_and_is_read(tmp_path):
    assert llm.load_tasks(_write_task(tmp_path, {}))["t"].max_input_chars == 7000
    assert llm.load_tasks(_write_task(tmp_path, None))["t"].max_input_chars == 7000
    assert llm.load_tasks(_write_task(tmp_path, {"max_input_chars": 12000}))[
        "t"
    ].max_input_chars == (12000)


@pytest.mark.parametrize("bad", [0, -1, "7000", 7000.5, True, None])
def test_max_input_chars_rejects_non_positive_int(tmp_path, bad):
    with pytest.raises(ValueError, match="max_input_chars"):
        llm.load_tasks(_write_task(tmp_path, {"max_input_chars": bad}))


def test_page_text_honours_task_max_input_chars(tmp_path):
    html = "<body>" + "".join(f"<p>line {i:04d}</p>" for i in range(3000)) + "</body>"
    task = llm.load_tasks(_write_task(tmp_path, {"max_input_chars": 500}))["t"]
    text = page_text(html, task.max_input_chars)
    assert 490 <= len(text) <= 500
    assert all(len(line) == 9 for line in text.split("\n"))  # cut at a line boundary only
    routed = llm.load_tasks("config/models.yaml")["extract_events"]
    assert len(page_text(html, routed.max_input_chars)) <= 7000


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


# ---------------------------------------------------------------- Scaleway provider (WIP-63)

SCW_KEY = "scw-secret-key-for-tests"
PROJECT = "78e655b5-feb0-417c-bb3f-8c448bd0e8da"  # example id from Scaleway's docs
SCW_URL = f"https://api.scaleway.ai/{PROJECT}/v1/chat/completions"


def scw(monkeypatch, project=PROJECT, **kw):
    monkeypatch.setenv("SCW_GENAI_SECRET_KEY", SCW_KEY)
    if project:
        monkeypatch.setenv("SCW_DEFAULT_PROJECT_ID", project)
    else:
        monkeypatch.delenv("SCW_DEFAULT_PROJECT_ID", raising=False)
    d = {"provider": "scaleway", "model": "mistral-small-3.2-24b-instruct-2506", **kw}
    return llm.ModelSpec.from_dict(d)


@respx.mock
def test_scaleway_request_shape(monkeypatch, caplog):
    caplog.set_level("DEBUG")
    spec = scw(monkeypatch, extra={"reasoning_effort": "none"})
    route = respx.post(SCW_URL).mock(return_value=reply({"events": [EVENT]}))
    msgs = [{"role": "user", "content": "page text"}]
    a = llm.chat_json(spec, msgs, SCHEMA, max_tokens=2048)
    assert a.data == {"events": [EVENT]}
    assert (a.tokens_in, a.tokens_out, a.attempts) == (100, 20, 1)
    req = route.calls[0].request
    assert req.headers["Authorization"] == f"Bearer {SCW_KEY}"
    sent = json.loads(req.content)
    assert sent["model"] == "mistral-small-3.2-24b-instruct-2506"
    assert sent["max_tokens"] == 2048 and sent["reasoning_effort"] == "none"
    fmt = sent["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == SCHEMA
    assert SCW_KEY not in caplog.text and "page text" not in caplog.text


def test_scaleway_url_default_project_and_bad_id(monkeypatch):
    assert scw(monkeypatch, project=None).url == "https://api.scaleway.ai/v1"
    assert scw(monkeypatch).url == f"https://api.scaleway.ai/{PROJECT}/v1"
    for bad in ("x/../evil", "78e655b5-feb0-417c-bb3f-8c448bd0e8d-", "-" * 36):
        spec = scw(monkeypatch, project=bad)
        with pytest.raises(llm.ModelError, match="not a project id"):
            _ = spec.url
        assert spec.available() == (False, "SCW_DEFAULT_PROJECT_ID is not a project id (UUID)")
    assert scw(monkeypatch, project=PROJECT.upper()).available() == (True, "ok")


@respx.mock
def test_extra_cannot_override_prompt_or_schema(monkeypatch):
    with pytest.raises(ValueError, match="messages, response_format"):
        scw(monkeypatch, extra={"messages": [], "response_format": {"type": "text"}})
    # built directly (not from config): reserved keys are dropped, the rest is sent
    spec = llm.ModelSpec(
        provider="local",
        model="small",
        base_url="http://llm.test/v1",
        extra={"messages": [{"role": "user", "content": "injected"}], "top_p": 0.9},
    )
    route = respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=reply({"events": []})
    )
    llm.chat_json(spec, [{"role": "user", "content": "real"}], SCHEMA)
    sent = json.loads(route.calls[0].request.content)
    assert sent["messages"] == [{"role": "user", "content": "real"}] and sent["top_p"] == 0.9
    assert sent["response_format"]["json_schema"]["schema"] == SCHEMA


def test_scaleway_without_key_is_skipped(monkeypatch):
    monkeypatch.delenv("SCW_GENAI_SECRET_KEY", raising=False)
    spec = llm.ModelSpec.from_dict({"provider": "scaleway", "model": "m"})
    assert spec.available() == (False, "no key (SCW_GENAI_SECRET_KEY not set)")
    with pytest.raises(llm.ModelError, match="not set"):
        llm.chat_json(spec, [], SCHEMA)


@respx.mock
def test_retries_on_429_and_5xx_then_succeeds(monkeypatch, caplog):
    spec = scw(monkeypatch)
    route = respx.post(SCW_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}, text="slow down: page text"),
            httpx.Response(503),
            reply({"events": []}),
        ]
    )
    waits = []
    a = llm.chat_json(spec, [], SCHEMA, sleep=waits.append)
    assert a.data == {"events": []} and a.attempts == 3 and route.call_count == 3
    assert waits == [7.0, 4.0]  # Retry-After wins, else 2 s * 2^(attempt-1)
    assert "page text" not in caplog.text and SCW_KEY not in caplog.text


@respx.mock
def test_retries_are_bounded(monkeypatch):
    spec = scw(monkeypatch)
    route = respx.post(SCW_URL).mock(return_value=httpx.Response(502))
    with pytest.raises(llm.ModelError, match="HTTP 502"):
        llm.chat_json(spec, [], SCHEMA, sleep=lambda s: None)
    assert route.call_count == llm.MAX_ATTEMPTS


@respx.mock
def test_client_error_is_not_retried_and_hides_body(monkeypatch):
    spec = scw(monkeypatch)
    route = respx.post(SCW_URL).mock(return_value=httpx.Response(401, text=f"bad key {SCW_KEY}"))
    with pytest.raises(llm.ModelError) as exc:
        llm.chat_json(spec, [], SCHEMA, sleep=lambda s: None)
    assert route.call_count == 1 and str(exc.value).endswith("HTTP 401")
    assert SCW_KEY not in str(exc.value)


@respx.mock
def test_timeout_is_not_retried_but_connect_error_is(monkeypatch):
    spec = scw(monkeypatch)
    slow = respx.post(SCW_URL).mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(llm.ModelError, match="ReadTimeout"):
        llm.chat_json(spec, [], SCHEMA, sleep=lambda s: None)
    assert slow.call_count == 1
    flaky = respx.post(SCW_URL).mock(side_effect=[httpx.ConnectError("c"), reply({"events": []})])
    assert llm.chat_json(spec, [], SCHEMA, sleep=lambda s: None).attempts == 2
    assert flaky is slow and flaky.call_count == 1 + 2  # respx reuses the route


def test_strict_schema_requires_every_property():
    assert llm.strict_schema_errors(SCHEMA) == []
    loose = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": ["string", "null"]}},
        "required": ["a"],
    }
    errors = llm.strict_schema_errors(loose)
    assert "$: not required: b" in errors and "$: additionalProperties must be false" in errors
    with pytest.raises(llm.ModelError, match="strict mode"):
        llm.chat_json(task().primary, [], loose)
