"""Scaleway Serverless Function: POST /feedback (ADR-0005).

Receives the listener's ratings from the page and stores them in the `feedback` table.
Security: CORS limited to the Pages origin, bearer token compared by SHA-256 hash, strict
schema and size limits, parameterised SQL. Never logs the token or the body.

Environment: ALLOWED_ORIGIN, FEEDBACK_TOKEN_SHA256 (secret), DATABASE_URL (secret).
Dependencies live in package/ (Scaleway's Python convention: added to the import path).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "package"))  # no-op when already on the path

import psycopg  # noqa: E402  (imported at load time: a broken package fails every call, loudly)

log = logging.getLogger("feedback")

DEFAULT_ORIGIN = "https://williampenet.github.io"
MAX_BODY = 4096
MAX_ITEMS = 50
MAX_KEYS = 12
KINDS = {"like", "unlike", "dislike", "wrong"}
CONCERT_ID_RE = re.compile(r"^[0-9a-f]{12}$")  # dedup.concert_id: 12 hex chars
ARTIST_KEY_RE = re.compile(r"^[a-z0-9]{1,100}$")  # artists.norm: lower-case alphanumerics
RATE_LIMIT = 300  # rows per minute for the (single, personal) token (ADR-0005)


class Invalid(ValueError):
    pass


class RateLimited(Exception):
    pass


def _response(status: int, origin: str | None, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json", "Vary": "Origin"}
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
    return {"statusCode": status, "headers": headers, "body": json.dumps(body or {})}


def validate(raw: bytes) -> list[tuple[str | None, str | None, str]]:
    """The rows (concert_id, artist_key, kind) to insert; raises Invalid with a short reason."""
    if len(raw) > MAX_BODY:
        raise Invalid("body too large")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Invalid("invalid json") from exc
    if not isinstance(data, dict) or set(data) != {"items"}:
        raise Invalid("expected {items}")
    items = data["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise Invalid(f"items: 1 to {MAX_ITEMS}")
    rows = []
    for item in items:
        if not isinstance(item, dict) or not set(item) <= {"kind", "concert_id", "artist_keys"}:
            raise Invalid("unexpected item field")
        kind = item.get("kind")
        if kind not in KINDS:
            raise Invalid("bad kind")
        cid = item.get("concert_id")
        if cid is not None and not (isinstance(cid, str) and CONCERT_ID_RE.match(cid)):
            raise Invalid("bad concert_id")
        keys = item.get("artist_keys", [])
        if not isinstance(keys, list) or len(keys) > MAX_KEYS:
            raise Invalid(f"artist_keys: at most {MAX_KEYS}")
        if not all(isinstance(k, str) and ARTIST_KEY_RE.match(k) for k in keys):
            raise Invalid("bad artist key")
        if cid is None and not keys:
            raise Invalid("concert_id or artist_keys required")
        rows.extend((cid, k, kind) for k in dict.fromkeys(keys))
        if not keys:
            rows.append((cid, None, kind))
    return rows


def process(method: str, headers: dict, body: bytes, store) -> dict:
    """Pure request handling; `store(rows) -> int` writes the rows."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    allowed = os.environ.get("ALLOWED_ORIGIN", DEFAULT_ORIGIN)
    if h.get("origin") != allowed:
        return _response(403, None, {"error": "origin not allowed"})
    if method == "OPTIONS":
        r = _response(204, allowed)
        r["body"] = ""
        r["headers"].update(
            {
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "86400",
            }
        )
        return r
    if method != "POST":
        return _response(405, allowed, {"error": "method not allowed"})
    expected = os.environ.get("FEEDBACK_TOKEN_SHA256", "")
    auth = h.get("authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not (expected and token and hmac.compare_digest(digest, expected.lower())):
        return _response(401, allowed, {"error": "unauthorized"})
    try:
        rows = validate(body)
    except Invalid as exc:
        return _response(400, allowed, {"error": str(exc)})
    try:
        stored = store(rows)
    except RateLimited:
        return _response(429, allowed, {"error": "too many ratings, retry later"})
    except Exception as exc:  # the DB may be waking up or down: the page keeps its queue
        log.warning("store failed: %s", type(exc).__name__)
        return _response(503, allowed, {"error": "storage unavailable"})
    return _response(202, allowed, {"stored": stored})


def pg_store(rows: list[tuple[str | None, str | None, str]]) -> int:
    """Inserts the rows in one transaction, unless the last minute already holds too many."""
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM feedback WHERE created_at > now() - interval '1 minute'"
            )
            if cur.fetchone()[0] + len(rows) > RATE_LIMIT:
                raise RateLimited
            cur.executemany(
                "INSERT INTO feedback (concert_id, artist_key, kind) VALUES (%s, %s, %s)", rows
            )
    return len(rows)


def handle(event, context):
    """Scaleway entry point (handler: handler.handle)."""
    body = event.get("body") or ""
    try:  # a truncated oversize body still decodes to more than MAX_BODY bytes
        raw = base64.b64decode(body[:8192]) if event.get("isBase64Encoded") else body.encode()
    except ValueError:
        raw = b""
    return process(event.get("httpMethod", ""), event.get("headers") or {}, raw, pg_store)
