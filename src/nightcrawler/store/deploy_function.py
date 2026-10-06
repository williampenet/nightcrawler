"""Package and deploy the feedback function on Scaleway Serverless Functions (ADR-0005).

Scaleway Functions REST API (v1beta1, fr-par), same API key as the event store. Idempotent:
the namespace and the function are reused and their settings updated; the code is uploaded
and deployed on every run. Secrets go to `secret_environment_variables` and are never printed.

Python dependencies are vendored into `package/` next to handler.py (Scaleway's convention
for the Python runtimes), as manylinux x86_64 wheels for the runtime's Python version.
"""

from __future__ import annotations

import logging
import re
import secrets
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

from .provision import REGION, Credentials, ProvisionError, _check, _client

log = logging.getLogger(__name__)

HANDLER_DIR = Path(__file__).resolve().parents[3] / "functions" / "feedback"
NAMESPACE = "nightcrawler"
FUNCTION = "feedback"
RUNTIME, PY_VERSION = "python312", "3.12"
REQUIREMENTS = ["psycopg[binary]==3.3.6"]  # pinned (ADR-0005 supply chain)
BASE = f"/functions/v1beta1/regions/{REGION}"


def build_zip(dest: Path, handler_dir: Path = HANDLER_DIR, install=None) -> Path:
    """handler.py + package/ (dependencies) zipped into dest."""
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "package"
        (install or _pip_install)(pkg)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(handler_dir / "handler.py", "handler.py")
            for path in sorted(pkg.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    zf.write(path, Path("package") / path.relative_to(pkg))
    return dest


def _pip_install(target: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(target),
         "--platform", "manylinux2014_x86_64", "--only-binary=:all:",
         "--python-version", PY_VERSION, *REQUIREMENTS],
        check=True,
    )  # fmt: skip


def _wait(client: httpx.Client, path: str, what: str, wait_s: float, ok=("ready",)) -> dict:
    deadline = time.monotonic() + wait_s
    while True:
        obj = _check(client.get(path), what)
        status = obj.get("status")
        if status in ok:
            return obj
        if status == "error" or time.monotonic() > deadline:
            msg = obj.get("error_message") or obj.get("build_message") or ""
            raise ProvisionError(f"{what} status: {status} {msg[:200]}".rstrip())
        time.sleep(5)


def ensure_namespace(client: httpx.Client, creds: Credentials, wait_s: float = 300) -> dict:
    found = _check(
        client.get(
            f"{BASE}/namespaces", params={"name": NAMESPACE, "project_id": creds.project_id}
        ),
        "list namespaces",
    )
    ns = next((n for n in found.get("namespaces", []) if n.get("name") == NAMESPACE), None)
    if ns is None:
        log.info("creating functions namespace %s", NAMESPACE)
        ns = _check(
            client.post(
                f"{BASE}/namespaces", json={"name": NAMESPACE, "project_id": creds.project_id}
            ),
            "create namespace",
        )
    return _wait(client, f"{BASE}/namespaces/{ns['id']}", "namespace", wait_s)


def function_settings(origin: str, database_url: str, token_sha256: str) -> dict:
    return {
        "runtime": RUNTIME,
        "handler": "handler.handle",
        "min_scale": 0,
        "max_scale": 1,
        "memory_limit": 128,
        "privacy": "public",
        "http_option": "redirected",  # HTTP → HTTPS
        "environment_variables": {"ALLOWED_ORIGIN": origin},
        "secret_environment_variables": [
            {"key": "DATABASE_URL", "value": database_url},
            {"key": "FEEDBACK_TOKEN_SHA256", "value": token_sha256},
        ],
    }


def upload_headers(up: dict) -> httpx.Headers:
    """Headers the presigned upload URL was signed with, once each (case-insensitive)."""
    headers = httpx.Headers()
    for k, v in (up.get("headers") or {}).items():
        headers[k] = v[0] if isinstance(v, list) and v else str(v)
    if "content-type" not in headers:
        headers["Content-Type"] = "application/octet-stream"
    return headers


def deploy(creds: Credentials, settings: dict, archive: Path, wait_s: float = 600) -> dict:
    """Ensure namespace + function, upload the archive, deploy; returns the ready function."""
    with _client(creds) as client:
        ns = ensure_namespace(client, creds)
        found = _check(
            client.get(f"{BASE}/functions", params={"namespace_id": ns["id"], "name": FUNCTION}),
            "list functions",
        )
        fn = next((f for f in found.get("functions", []) if f.get("name") == FUNCTION), None)
        if fn is None:
            log.info("creating function %s", FUNCTION)
            fn = _check(
                client.post(
                    f"{BASE}/functions",
                    json={"name": FUNCTION, "namespace_id": ns["id"], **settings},
                ),
                "create function",
            )
        else:
            fn = _check(client.patch(f"{BASE}/functions/{fn['id']}", json=settings), "update")
        path = f"{BASE}/functions/{fn['id']}"
        _wait(client, path, "function", wait_s, ok=("ready", "created"))
        data = archive.read_bytes()
        up = _check(
            client.get(f"{path}/upload-url", params={"content_length": len(data)}), "upload url"
        )
        put = httpx.put(up["url"], content=data, headers=upload_headers(up), timeout=120)
        if put.status_code >= 400:
            # S3 error codes are safe to show; the body itself may echo signing details
            code = re.search(r"<Code>([A-Za-z]+)</Code>", put.text or "")
            names = ",".join(sorted(upload_headers(up).keys()))
            raise ProvisionError(
                f"upload: HTTP {put.status_code} {code.group(1) if code else ''} "
                f"(headers sent: {names})"
            )
        _check(client.post(f"{path}/deploy", json={}), "deploy")  # status becomes pending
        return _wait(client, path, "function", wait_s)


def smoke_test(url: str, origin: str, tries: int = 6) -> tuple[int, int]:
    """CORS preflight (cold start allowed), then a POST with a random wrong token, which must
    be refused (401) before anything is written. Returns both HTTP statuses (0: no answer)."""
    preflight = 0
    for _ in range(tries):
        try:
            preflight = httpx.options(
                url,
                headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
                timeout=30,
            ).status_code
        except httpx.HTTPError:
            preflight = 0
        if preflight == 204:
            break
        time.sleep(5)
    try:
        refused = httpx.post(
            url,
            headers={"Origin": origin, "Authorization": f"Bearer {secrets.token_urlsafe(24)}"},
            json={"items": [{"kind": "like", "artist_keys": ["smoketest"]}]},
            timeout=30,
        ).status_code
    except httpx.HTTPError:
        refused = 0
    return preflight, refused
