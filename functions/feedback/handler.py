"""Scaleway Serverless Function: POST / (feedback), GET/PUT /profile (ADR-0005, WIP-46) and
GET /verdicts (ADR-0007, WIP-85).

POST receives the listener's ratings and stores them in the `feedback` table. /profile keeps
the taste profile (seed artists, ratings, hidden concerts, written taste) so it follows the
listener across devices; PUT uses optimistic concurrency (base_version, 409 with the current
profile). /verdicts serves the taste judgements of concerts starting from yesterday on
(personal data: no-store, never logged beyond a count).
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
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "package"))  # no-op when already on the path

import psycopg  # noqa: E402  (imported at load time: a broken package fails every call, loudly)
from psycopg.types.json import Jsonb  # noqa: E402

log = logging.getLogger("feedback")

DEFAULT_ORIGIN = "https://williampenet.github.io"
MAX_BODY = 4096
MAX_ITEMS = 50
MAX_KEYS = 12
KINDS = {"like", "unlike", "dislike", "wrong"}
CONCERT_ID_RE = re.compile(r"^[0-9a-f]{12}$")  # dedup.concert_id: 12 hex chars
ARTIST_KEY_RE = re.compile(r"^[a-z0-9]{1,100}$")  # artists.norm: lower-case alphanumerics
RATE_LIMIT = 300  # rows per minute for the (single, personal) token (ADR-0005)
# profile (WIP-46): same shape and limits as the page's sanitizeState / profile.js
MAX_PROFILE_BODY = 65536
PROFILE_RATE_LIMIT = 60  # PUTs per minute; the page debounces to at most one per 1.5 s
MAX_SEEDS, MAX_NAME, MAX_TAGS, MAX_TAG, MAX_LIST = 200, 60, 12, 100, 2000
KEY_LISTS = ("liked", "disliked", "wrong", "likedNames", "dislikedNames")
ID_LISTS = ("hidden", "likedConcerts")
# saved concert ids are kept even when absent from a day's data (WIP-59): the page keeps
# the 500 most recent per list (scoring.js MAX_IDS) and the function enforces the same cap
MAX_IDS = 500
# "Mon goût en mots" (WIP-73): the listener's written taste profile (PRD FR-4), personal data,
# stored here only and never logged. 4,000 characters (code points; the page counts UTF-16
# code units, so its text is never longer here). The text alone is at most 24 KB of JSON
# (4,000 escapes of 6 bytes); seeds and artist lists are not bounded under MAX_PROFILE_BODY
# with it, so a large profile gets 400 "body too large" (the page says so).
# taste_text_at: the edit time (ms since epoch, the browser's clock) of the last-writer-wins
# merge, applied by the page and again on the locked row (keep_taste_text).
MAX_TASTE_TEXT = 4000
MAX_TIMESTAMP = 2**53 - 1  # JavaScript's Number.MAX_SAFE_INTEGER
PROFILE_FIELDS = ("seeds", *KEY_LISTS, *ID_LISTS, "taste_text", "taste_text_at")
# verdicts (ADR-0007 Decision 4): one indexed read of at most a few hundred rows
VERDICTS_STATEMENT_TIMEOUT = "10s"


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


def validate_profile(raw: bytes) -> tuple[dict, int]:
    """(data, base_version) of a PUT /profile body; raises Invalid. Missing lists become []."""
    if len(raw) > MAX_PROFILE_BODY:
        raise Invalid("body too large")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Invalid("invalid json") from exc
    if not isinstance(body, dict) or set(body) != {"data", "base_version"}:
        raise Invalid("expected {data, base_version}")
    base, data = body["base_version"], body["data"]
    if not isinstance(base, int) or isinstance(base, bool) or base < 0:
        raise Invalid("bad base_version")
    if not isinstance(data, dict) or not set(data) <= set(PROFILE_FIELDS):
        raise Invalid("unexpected profile field")
    out: dict = {}
    seeds = data.get("seeds", [])
    if not isinstance(seeds, list) or len(seeds) > MAX_SEEDS:
        raise Invalid(f"seeds: at most {MAX_SEEDS}")
    out["seeds"] = []
    for seed in seeds:
        if not isinstance(seed, dict) or not set(seed) <= {"name", "tags"}:
            raise Invalid("bad seed")
        name, tags = seed.get("name"), seed.get("tags")
        if not (isinstance(name, str) and 0 < len(name) <= MAX_NAME):
            raise Invalid("bad seed name")
        if tags is not None and not (
            isinstance(tags, list)
            and len(tags) <= MAX_TAGS
            and all(isinstance(t, str) and 0 < len(t) <= MAX_TAG for t in tags)
        ):
            raise Invalid("bad seed tags")
        out["seeds"].append({"name": name, "tags": tags})
    for field, pattern, cap in [(f, ARTIST_KEY_RE, MAX_LIST) for f in KEY_LISTS] + [
        (f, CONCERT_ID_RE, MAX_IDS) for f in ID_LISTS
    ]:
        values = data.get(field, [])
        if not isinstance(values, list) or len(values) > cap:
            raise Invalid(f"{field}: at most {cap}")
        if not all(isinstance(v, str) and pattern.match(v) for v in values):
            raise Invalid(f"bad {field} item")
        out[field] = list(dict.fromkeys(values))
    text = data.get("taste_text", "")
    # PostgreSQL's jsonb rejects \u0000 (https://www.postgresql.org/docs/current/datatype-json.html)
    if not (isinstance(text, str) and len(text) <= MAX_TASTE_TEXT and "\x00" not in text):
        raise Invalid(f"taste_text: a string of at most {MAX_TASTE_TEXT} characters")
    try:  # a lone surrogate ("\ud800" in the JSON) is not text: it cannot be stored as UTF-8
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise Invalid("taste_text: not valid unicode") from exc
    at = data.get("taste_text_at", 0)
    if not isinstance(at, int) or isinstance(at, bool) or not 0 <= at <= MAX_TIMESTAMP:
        raise Invalid("bad taste_text_at")
    if "taste_text" in data:  # absent (a page older than WIP-73): the stored text is kept
        out["taste_text"], out["taste_text_at"] = text, at
    return out, base


def keep_taste_text(data: dict, stored: dict | None) -> dict:
    """`data` to store, with the written taste the stored row keeps (WIP-73).

    A body without `taste_text` keeps the stored text; a body with it replaces the stored text
    only if its `taste_text_at` is not older (a tie goes to the request, as on the page), so
    only an explicit "" with an edit time at least as recent clears it."""
    stored = stored or {}
    if "taste_text" in stored and (
        "taste_text" not in data or data["taste_text_at"] < stored.get("taste_text_at", 0)
    ):
        return {
            **data,
            "taste_text": stored["taste_text"],
            "taste_text_at": stored.get("taste_text_at", 0),
        }
    return data


def _profile(method: str, body: bytes, profile, allowed: str) -> dict:
    """GET/PUT /profile; `profile` has get() -> dict | None and put(data, base) -> (ok, row)."""
    try:
        if method == "GET":
            row = profile.get()
            # no profile yet: 200 with version 0, so a gateway 404 is never read as "none"
            return _response(200, allowed, row or {"data": None, "version": 0})
        if method != "PUT":
            return _response(405, allowed, {"error": "method not allowed"})
        try:
            data, base = validate_profile(body)
        except Invalid as exc:
            return _response(400, allowed, {"error": str(exc)})
        ok, row = profile.put(data, base)
    except RateLimited:
        return _response(429, allowed, {"error": "too many updates, retry later"})
    except Exception as exc:  # the DB may be waking up or down: the page keeps its copy
        log.warning("profile store failed: %s", type(exc).__name__)
        return _response(503, allowed, {"error": "storage unavailable"})
    if ok:
        return _response(200, allowed, {"version": row["version"]})
    return _response(
        409,
        allowed,
        {"data": row["data"] if row else None, "version": row["version"] if row else 0},
    )


def _verdicts(method: str, read, allowed: str) -> dict:
    """GET /verdicts; `read() -> {concert id: {section, verdict, confidence, reason}}`.

    Always `Cache-Control: no-store` (personal data, ADR-0007); logs a count at most."""
    if method != "GET":
        r = _response(405, allowed, {"error": "method not allowed"})
    else:
        try:
            verdicts = read()
        except Exception as exc:  # the DB may be waking up or down: the page keeps its copy
            log.warning("verdicts read failed: %s", type(exc).__name__)
            r = _response(503, allowed, {"error": "storage unavailable"})
        else:
            log.info("verdicts served: %d", len(verdicts))
            now = datetime.now(UTC).isoformat(timespec="seconds")
            r = _response(200, allowed, {"generated_at": now, "verdicts": verdicts})
    r["headers"]["Cache-Control"] = "no-store"
    return r


def process(
    method: str,
    headers: dict,
    body: bytes,
    store,
    path: str = "/",
    profile=None,
    verdicts=None,
) -> dict:
    """Pure request handling; `store(rows) -> int` writes the rows; `profile`: see _profile;
    `verdicts`: see _verdicts."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    allowed = os.environ.get("ALLOWED_ORIGIN", DEFAULT_ORIGIN)
    if h.get("origin") != allowed:
        return _response(403, None, {"error": "origin not allowed"})
    if method == "OPTIONS":
        r = _response(204, allowed)
        r["body"] = ""
        r["headers"].update(
            {
                "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "86400",
            }
        )
        return r
    route = path.strip("/").split("/")[-1]
    is_profile = route == "profile"
    if method != "POST" and route not in ("profile", "verdicts"):
        return _response(405, allowed, {"error": "method not allowed"})
    expected = os.environ.get("FEEDBACK_TOKEN_SHA256", "")
    auth = h.get("authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not (expected and token and hmac.compare_digest(digest, expected.lower())):
        return _response(401, allowed, {"error": "unauthorized"})
    if is_profile:
        return _profile(method, body, profile or PgProfile(), allowed)
    if route == "verdicts":
        return _verdicts(method, verdicts or pg_verdicts, allowed)
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


def pg_verdicts() -> dict[str, dict]:
    """The stored judgements of concerts starting from yesterday on, in a read-only transaction
    with a statement timeout (ADR-0007 Decision 4)."""
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15, autocommit=True) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET LOCAL statement_timeout = '{VERDICTS_STATEMENT_TIMEOUT}'")
            rows = conn.execute(
                "SELECT concert_id, section, verdict, confidence, reason FROM verdicts "
                "WHERE starts_at >= now() - interval '1 day' ORDER BY concert_id"
            ).fetchall()
    return {
        cid: {"section": section, "verdict": verdict, "confidence": int(conf), "reason": reason}
        for cid, section, verdict, conf, reason in rows
    }


class PgProfile:
    """The single profile row, with optimistic concurrency on `version`."""

    def get(self) -> dict | None:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as conn:
            row = conn.execute(
                "SELECT data, version, updated_at FROM profile WHERE id = 'me'"
            ).fetchone()
        if not row:
            return None
        return {"data": row[0], "version": row[1], "updated_at": row[2].isoformat()}

    def put(self, data: dict, base: int) -> tuple[bool, dict | None]:
        """(True, {version}) if base matches (0: no row yet), else (False, current or None)."""
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM profile_writes WHERE at < now() - interval '1 hour'")
            cur.execute(
                "SELECT count(*) FROM profile_writes WHERE at > now() - interval '1 minute'"
            )
            if cur.fetchone()[0] >= PROFILE_RATE_LIMIT:
                raise RateLimited
            cur.execute("SELECT data, version FROM profile WHERE id = 'me' FOR UPDATE")
            row = cur.fetchone()
            current = {"data": row[0], "version": row[1]} if row else None
            if (current["version"] if current else 0) != base:
                return False, current
            payload = Jsonb(keep_taste_text(data, current["data"] if current else None))
            if current:
                cur.execute(
                    "UPDATE profile SET data = %s, version = version + 1, updated_at = now() "
                    "WHERE id = 'me' RETURNING version",
                    (payload,),
                )
            else:  # a concurrent first upload wins the race: this one gets a 409
                cur.execute(
                    "INSERT INTO profile (data) VALUES (%s) ON CONFLICT (id) DO NOTHING "
                    "RETURNING version",
                    (payload,),
                )
            done = cur.fetchone()
            if not done:
                conn.rollback()  # the concurrent first upload is committed: read it back
                cur.execute("SELECT data, version FROM profile WHERE id = 'me'")
                row = cur.fetchone()
                return False, {"data": row[0], "version": row[1]} if row else None
            cur.execute("INSERT INTO profile_writes DEFAULT VALUES")
        return True, {"version": done[0]}


def handle(event, context):
    """Scaleway entry point (handler: handler.handle).

    `httpMethod`: measured on the 2026-10-06 deploy ("Feedback function" workflow run on
    commit 066fe2e: preflight HTTP 204, wrong-token POST HTTP 401; without `httpMethod` both
    would be 405). Unverified: Scaleway's docs list `path` and `method`
    (https://www.scaleway.com/en/docs/serverless-functions/reference-content/code-examples/),
    so `method`, `path` and `rawPath` are read too, and "/profile" and "profile" (likewise
    "/verdicts") are routed; the deploy smoke test (GET /profile and GET /verdicts -> 401,
    GET / -> 405) checks the routing."""
    body = event.get("body") or ""
    try:  # a truncated oversize body still decodes to more than the size limits
        cut = body[: MAX_PROFILE_BODY * 2]
        raw = base64.b64decode(cut) if event.get("isBase64Encoded") else cut.encode()
    except ValueError:
        raw = b""
    method = event.get("httpMethod") or event.get("method") or ""
    path = event.get("path") or event.get("rawPath") or "/"
    return process(method, event.get("headers") or {}, raw, pg_store, str(path))
