"""Provider abstraction for runtime LLM calls (CLAUDE.md, LLM policy).

Business code never names a model: it calls `run_task(task, messages, schema, check)` with a
task loaded from `config/models.yaml`. Every provider speaks the OpenAI-compatible chat API
(llama.cpp `llama-server` locally, Mistral's EU API, Scaleway Generative APIs in Paris), so
switching model = editing config.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    base_url: str  # default; a `{project_id}` placeholder is filled from `project_env`
    key_env: str | None = None  # env var holding the API key (never logged)
    project_env: str | None = None


PROVIDERS: dict[str, Provider] = {
    "local": Provider("http://127.0.0.1:8080/v1"),
    "mistral": Provider("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    # OpenAI-compatible; project-scoped URL `https://api.scaleway.ai/<project id>/v1`, the id is
    # hidden for the default project (scaleway.com/en/docs/generative-apis/api-cli/
    # using-generative-apis.md). Key: IAM application limited to GenerativeApisModelAccess.
    "scaleway": Provider(
        "https://api.scaleway.ai/{project_id}/v1", "SCW_GENAI_SECRET_KEY", "SCW_DEFAULT_PROJECT_ID"
    ),
}
HF_URL = "https://huggingface.co/{repo}/resolve/{revision}/{file}"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
BACKOFF_S = 2.0  # 2 s, 4 s; a Retry-After header wins, capped at MAX_WAIT_S
MAX_WAIT_S = 30.0
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# request fields owned by chat_json; a config `extra` may not set them
RESERVED_EXTRA = frozenset({"model", "messages", "response_format", "stream"})


class ModelError(RuntimeError):
    """The model could not be reached or answered with something unusable."""


@dataclass
class ModelSpec:
    provider: str
    model: str
    revision: str | None = None
    repo: str | None = None  # Hugging Face repo of the GGUF weights (local models)
    file: str | None = None
    sha256: str | None = None
    base_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # provider-specific request fields

    @classmethod
    def from_dict(cls, d: dict) -> ModelSpec:
        if d.get("provider") not in PROVIDERS:
            raise ValueError(f"unknown provider {d.get('provider')!r}")
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        if bad := sorted(RESERVED_EXTRA & set(known.get("extra") or {})):
            raise ValueError(f"extra may not set {', '.join(bad)}")
        return cls(**known)

    def project_id(self) -> str:
        """Project id for a project-scoped URL ('' = default project); ModelError if malformed."""
        env = PROVIDERS[self.provider].project_env
        project = os.environ.get(env or "", "").strip()
        if project and not UUID_RE.fullmatch(project):
            raise ModelError(f"{env} is not a project id (UUID)")
        return project

    @property
    def url(self) -> str:
        p = PROVIDERS[self.provider]
        if self.base_url:
            url = self.base_url
        elif self.provider == "local" and os.environ.get("LLM_LOCAL_URL"):
            url = os.environ["LLM_LOCAL_URL"]
        else:
            url = p.base_url
        if "{project_id}" in url:
            project = self.project_id()
            url = (
                url.replace("{project_id}", project)
                if project
                else url.replace("/{project_id}", "")
            )
        return url.rstrip("/")

    @property
    def key_env(self) -> str | None:
        return PROVIDERS[self.provider].key_env

    def available(self) -> tuple[bool, str]:
        if self.key_env and not os.environ.get(self.key_env):
            return False, f"no key ({self.key_env} not set)"
        try:
            self.project_id()
        except ModelError as exc:
            return False, str(exc)
        return True, "ok"


@dataclass
class Task:
    name: str
    primary: ModelSpec
    fallback: ModelSpec | None = None
    adr: str | None = None
    max_output_tokens: int = 1024
    timeout_s: float = 120.0
    temperature: float = 0.0
    min_quality: float | None = None


def load_tasks(path: str | Path = "config/models.yaml") -> dict[str, Task]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    tasks = {}
    for name, t in (data.get("tasks") or {}).items():
        limits = t.get("limits") or {}
        tasks[name] = Task(
            name=name,
            primary=ModelSpec.from_dict(t["primary"]),
            fallback=ModelSpec.from_dict(t["fallback"]) if t.get("fallback") else None,
            adr=t.get("adr"),
            max_output_tokens=int(limits.get("max_output_tokens", 1024)),
            timeout_s=float(limits.get("timeout_ms", 120_000)) / 1000,
            temperature=float(t.get("temperature", 0.0)),
            min_quality=t.get("min_quality"),
        )
    return tasks


# ---------------------------------------------------------------- schema validation
# A deliberately small JSON Schema subset (the one our task schemas use): object, array,
# string, boolean, integer, null, type lists, required, additionalProperties: false,
# maxItems, maxLength, pattern. Output that does not fit is rejected, never repaired.


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    types = schema.get("type")
    if isinstance(types, list):
        if not any(not validate(value, {**schema, "type": t}, path) for t in types):
            return [f"{path}: expected one of {types}"]
        return []
    errors: list[str] = []
    if types == "null":
        return [] if value is None else [f"{path}: expected null"]
    if types == "boolean":
        return [] if isinstance(value, bool) else [f"{path}: expected boolean"]
    if types == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
        return [] if ok else [f"{path}: expected integer"]
    if types == "string":
        if not isinstance(value, str):
            return [f"{path}: expected string"]
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: not one of {schema['enum']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
        return errors
    if types == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array"]
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items", {}), f"{path}[{i}]")
        return errors
    if types == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object"]
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                errors.append(f"{path}: missing {k}")
        for k, v in value.items():
            if k in props:
                errors += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected {k}")
        return errors
    return [f"{path}: unsupported schema type {types!r}"]  # fail closed


def strict_schema_errors(schema: dict, path: str = "$") -> list[str]:
    """Strict `json_schema` mode (OpenAI-compatible APIs, Scaleway's structured-outputs guide):
    every object lists all its properties as required and sets additionalProperties: false;
    optional fields are expressed as nullable (`["string", "null"]`), never left out."""
    errors: list[str] = []
    types = schema.get("type")
    if types == "object" or (isinstance(types, list) and "object" in types):
        props = schema.get("properties", {})
        missing = sorted(set(props) - set(schema.get("required", [])))
        if missing:
            errors.append(f"{path}: not required: {', '.join(missing)}")
        if schema.get("additionalProperties") is not False:
            errors.append(f"{path}: additionalProperties must be false")
        for k, sub in props.items():
            errors += strict_schema_errors(sub, f"{path}.{k}")
    if "items" in schema:
        errors += strict_schema_errors(schema["items"], f"{path}[]")
    return errors


# ---------------------------------------------------------------- calls


@dataclass
class Answer:
    data: dict | None
    errors: list[str]
    model: str
    latency_s: float
    tokens_in: int = 0
    tokens_out: int = 0
    escalated: bool = False
    attempts: int = 1


def _retry_wait(attempt: int, r: httpx.Response | None) -> float:
    after = r.headers.get("Retry-After") if r is not None else None
    if after and after.strip().isdigit():
        return min(float(after), MAX_WAIT_S)
    return min(BACKOFF_S * 2 ** (attempt - 1), MAX_WAIT_S)


def chat_json(
    spec: ModelSpec,
    messages: list[dict],
    schema: dict,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    timeout_s: float = 120.0,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Answer:
    """One constrained-JSON chat completion. Never raises on a bad answer: errors are listed.

    HTTP 429 / 5xx and connection failures are retried (MAX_ATTEMPTS, exponential backoff or
    Retry-After); a read timeout is not (a slow model stays slow). Logs and errors carry the
    model id and the status only: never the prompt, the response body or the key.
    """
    if bad := strict_schema_errors(schema):
        raise ModelError(f"schema not usable in strict mode: {'; '.join(bad[:3])}")
    headers = {"Content-Type": "application/json"}
    if spec.key_env:
        key = os.environ.get(spec.key_env)
        if not key:
            raise ModelError(f"{spec.key_env} not set")
        headers["Authorization"] = f"Bearer {key}"
    body = {
        # first, and without reserved keys, so config can never replace the prompt or schema
        **{k: v for k, v in spec.extra.items() if k not in RESERVED_EXTRA},
        "model": spec.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "output", "schema": schema, "strict": True},
        },
    }
    url = f"{spec.url}/chat/completions"
    timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 15.0))
    own = client is None
    client = client or httpx.Client()
    try:
        # POST is not idempotent: a 5xx or a dropped connection may come after the provider has
        # generated (and billed) the answer, so a retry can be billed twice. At most 2 extra
        # answers per page: ≤ €0.007 at the dearest candidate (Qwen3.6, €3.63 per 1,000 pages
        # worst case, ADR-0004): negligible.
        for attempt in range(1, MAX_ATTEMPTS + 1):
            r = None
            t0 = time.monotonic()
            try:
                r = client.post(url, json=body, headers=headers, timeout=timeout)
            except httpx.TimeoutException as exc:
                raise ModelError(f"{spec.model}: {type(exc).__name__}") from exc
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise ModelError(f"{spec.model}: {type(exc).__name__}") from exc
                log.warning("%s: %s, retrying", spec.model, type(exc).__name__)
            latency = time.monotonic() - t0  # last attempt only: backoff is not model latency
            if r is not None and (r.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS):
                break
            if r is not None:
                log.warning("%s: HTTP %s, retrying", spec.model, r.status_code)
            (sleep or time.sleep)(_retry_wait(attempt, r))
    finally:
        if own:
            client.close()
    if r.status_code != 200:
        # the body may echo the prompt: keep only the status in logs
        raise ModelError(f"{spec.model}: HTTP {r.status_code}")
    try:
        payload = r.json()
    except ValueError as exc:
        raise ModelError(f"{spec.model}: response is not JSON") from exc
    usage = payload.get("usage") or {}
    answer = Answer(
        None,
        [],
        spec.model,
        latency,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
        attempts=attempt,
    )
    try:
        content = payload["choices"][0]["message"]["content"]
        data = _parse_json(content)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        answer.errors = [f"unparsable output: {type(exc).__name__}"]
        return answer
    answer.errors = validate(data, schema)
    answer.data = data if not answer.errors else None
    return answer


def _parse_json(content: str) -> Any:
    import json

    text = content.strip()
    # some chat templates still wrap constrained output in a fence or a think block
    text = re.sub(r"^<think>.*?</think>\s*", "", text, flags=re.S)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


def run_task(
    task: Task,
    messages: list[dict],
    schema: dict,
    check: Callable[[dict], list[str]] | None = None,
    *,
    client: httpx.Client | None = None,
) -> Answer:
    """Primary model; one retry on the fallback when the output fails schema or `check`.

    `answer.data` is None only when the output is not schema-valid; `check` errors are reported
    in `answer.errors` but the (valid) data is kept for the caller to filter.
    Transport failures (timeout, HTTP error) raise ModelError and are not escalated: the caller
    skips that input for this run.
    """

    def ask(spec: ModelSpec) -> Answer:
        a = chat_json(
            spec,
            messages,
            schema,
            max_tokens=task.max_output_tokens,
            temperature=task.temperature,
            timeout_s=task.timeout_s,
            client=client,
        )
        if a.data is not None and check:
            a.errors = check(a.data)  # data kept: the caller still filters it
        return a

    answer = ask(task.primary)
    if answer.errors and task.fallback and task.fallback.available()[0]:
        log.info("task %s: escalating to fallback (%s)", task.name, "; ".join(answer.errors[:2]))
        second = ask(task.fallback)
        second.escalated = True
        second.latency_s += answer.latency_s
        second.tokens_in += answer.tokens_in
        second.tokens_out += answer.tokens_out
        return second
    return answer


# ---------------------------------------------------------------- local weights and server


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_weights(spec: ModelSpec, cache_dir: str | Path = ".cache/models") -> Path:
    """Download pinned GGUF weights from the publisher's repo and verify their SHA-256."""
    if not (spec.repo and spec.file and spec.revision and spec.sha256):
        raise ModelError(f"{spec.model}: local weights need repo, file, revision and sha256")
    if not spec.file.endswith(".gguf"):
        raise ModelError(f"{spec.model}: only GGUF weights are accepted")
    dest = Path(cache_dir) / spec.sha256[:16] / spec.file
    if dest.exists() and sha256_of(dest) == spec.sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    url = HF_URL.format(repo=spec.repo, revision=spec.revision, file=spec.file)
    log.info("downloading %s", url)
    h = hashlib.sha256()
    with httpx.stream("GET", url, follow_redirects=True, timeout=600) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                h.update(chunk)
                fh.write(chunk)
    if h.hexdigest() != spec.sha256:
        tmp.unlink(missing_ok=True)
        raise ModelError(f"{spec.model}: SHA-256 mismatch, weights rejected")
    tmp.replace(dest)
    return dest


def _free_port() -> int:
    # small race (the port is released before llama-server binds it): fine on a CI runner
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaServer:
    """Runs llama.cpp's `llama-server` on CPU for one local model (context manager)."""

    def __init__(self, binary: str, weights: Path, ctx: int = 8192, threads: int | None = None):
        self.binary = shutil.which(binary) or binary
        self.weights = weights
        self.ctx = ctx
        self.threads = threads or os.cpu_count() or 2
        self.port = _free_port()
        self.proc: subprocess.Popen | None = None
        self.log_path = Path(weights).parent / "llama-server.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> LlamaServer:
        cmd = [
            self.binary,
            "-m",
            str(self.weights),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "-c",
            str(self.ctx),
            "-t",
            str(self.threads),
            "--jinja",
            "-np",
            "1",
        ]
        # a file, not a pipe: llama-server is verbose and a full pipe would block it
        self._log = self.log_path.open("wb")
        try:
            self.proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT)
        except OSError as exc:
            self._log.close()
            raise ModelError(f"cannot start llama-server: {type(exc).__name__}") from exc
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self.__exit__(None, None, None)
                err = self.log_path.read_bytes()[-400:].decode(errors="replace")
                raise ModelError(f"llama-server exited: {err}")
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=2).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(1)
        self.__exit__(None, None, None)
        raise ModelError("llama-server did not become ready")

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "_log", None):
            self._log.close()
