"""Create (once) the Scaleway Serverless SQL Database and give its connection URL.

Talks to the Scaleway REST API with the project's API key (CI secrets only):
SCW_ACCESS_KEY, SCW_SECRET_KEY, SCW_DEFAULT_PROJECT_ID. Idempotent: an existing database
with the same name is reused. Never prints the secret key.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

log = logging.getLogger(__name__)

API = "https://api.scaleway.com"
REGION = "fr-par"
DB_NAME = "nightcrawler"
CPU_MIN, CPU_MAX = 0, 1  # min 0 vCPU: not billed while idle (ADR-0005)


class ProvisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    access_key: str
    secret_key: str
    project_id: str

    @classmethod
    def from_env(cls) -> Credentials:
        names = ("SCW_ACCESS_KEY", "SCW_SECRET_KEY", "SCW_DEFAULT_PROJECT_ID")
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            raise ProvisionError("missing secrets: " + ", ".join(missing))
        return cls(*(os.environ[n] for n in names))


def _client(creds: Credentials) -> httpx.Client:
    return httpx.Client(base_url=API, headers={"X-Auth-Token": creds.secret_key}, timeout=30)


def _check(r: httpx.Response, what: str) -> dict:
    if r.status_code >= 400:
        # the body never contains the key; keep it short anyway
        raise ProvisionError(f"{what}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def principal_id(client: httpx.Client, creds: Credentials) -> str:
    """The IAM user or application that owns the API key: the database user name.

    SCW_DB_USER skips the lookup, for keys without IAM read rights on themselves.
    """
    if os.environ.get("SCW_DB_USER"):
        return os.environ["SCW_DB_USER"]
    key = _check(client.get(f"/iam/v1alpha1/api-keys/{creds.access_key}"), "api key")
    pid = key.get("application_id") or key.get("user_id")
    if not pid:
        raise ProvisionError("api key has no owner")
    return pid


def ensure_database(
    client: httpx.Client, creds: Credentials, wait_s: float = 600, create: bool = True
) -> dict:
    """The database, created first if missing (create=False: ProvisionError instead)."""
    base = f"/serverless-sqldb/v1alpha1/regions/{REGION}/databases"
    found = _check(
        client.get(base, params={"name": DB_NAME, "project_id": creds.project_id}), "list databases"
    )
    db = next((d for d in found.get("databases", []) if d.get("name") == DB_NAME), None)
    if db is None and not create:
        raise ProvisionError("database not found")
    if db is None:
        log.info("creating Serverless SQL Database %s in %s", DB_NAME, REGION)
        db = _check(
            client.post(
                base,
                json={
                    "name": DB_NAME,
                    "project_id": creds.project_id,
                    "cpu_min": CPU_MIN,
                    "cpu_max": CPU_MAX,
                },
            ),
            "create database",
        )
    deadline = time.monotonic() + wait_s
    while db.get("status") not in ("ready", "error", "locked") and time.monotonic() < deadline:
        time.sleep(5)
        db = _check(client.get(f"{base}/{db['id']}"), "get database")
    if db.get("status") != "ready":
        raise ProvisionError(f"database status: {db.get('status')}")
    return db


def connection_url(endpoint: str, user: str, password: str) -> str:
    """The database endpoint with IAM credentials inserted, TLS required."""
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}{port}"
    query = parts.query or "sslmode=require"
    if "sslmode=" not in query:
        query += "&sslmode=require"
    return urlunsplit(("postgresql", netloc, parts.path, query, ""))


def ensure(creds: Credentials | None = None, create: bool = True) -> tuple[dict, str]:
    """Returns the database description and its connection URL; create=False only looks
    the database up (read-only callers such as the taste eval, WIP-52)."""
    creds = creds or Credentials.from_env()
    with _client(creds) as client:
        user = principal_id(client, creds)
        db = ensure_database(client, creds, create=create)
    return db, connection_url(db["endpoint"], user, creds.secret_key)
