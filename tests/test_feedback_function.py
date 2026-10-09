import base64
import hashlib
import importlib.util
import json
import logging
import os
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import respx

from nightcrawler import cli
from nightcrawler.store import deploy_function as dfn
from nightcrawler.store import provision

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("handler", ROOT / "functions/feedback/handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)

ORIGIN = "https://williampenet.github.io"
CID = "0123456789ab"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOKEN_SHA256", hashlib.sha256(b"s3cret").hexdigest())
    monkeypatch.delenv("ALLOWED_ORIGIN", raising=False)


def call(method="POST", body=None, token="s3cret", origin=ORIGIN, store=None):
    headers = {"Origin": origin} if origin else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
    rows = []

    def fake(r):
        rows.extend(r)
        return len(r)

    resp = handler.process(method, headers, raw, store or fake)
    return resp, rows


def test_every_answer_is_no_store():
    # WIP-85 review: /profile carries the written taste, /verdicts the judgements; errors and
    # the preflight are not cached either (preflight caching is Access-Control-Max-Age)
    def down(*a):
        raise RuntimeError("down")

    def busy(*a):
        raise handler.RateLimited

    good = {"Origin": ORIGIN, "Authorization": "Bearer s3cret"}
    rating = json.dumps({"items": [{"kind": "like", "artist_keys": ["a"]}]}).encode()
    profile_put = json.dumps({"data": {}, "base_version": 3}).encode()

    class DownProfile:
        get = put = down

    answers = [
        handler.process("GET", {"Origin": "https://evil.example"}, b"", None, "/verdicts"),  # 403
        handler.process("OPTIONS", {"Origin": ORIGIN}, b"", None, "/profile"),  # 204
        handler.process("GET", good, b"", None, "/"),  # 405
        handler.process("POST", {"Origin": ORIGIN}, rating, None),  # 401
        handler.process("POST", good, b"{}", None),  # 400
        handler.process("POST", good, rating, lambda rows: len(rows)),  # 202
        handler.process("POST", good, rating, busy),  # 429
        handler.process("POST", good, rating, down),  # 503
        handler.process("GET", good, b"", None, "/profile", FakeProfile()),  # 200
        handler.process("PUT", good, profile_put, None, "/profile", FakeProfile()),  # 409
        handler.process("GET", good, b"", None, "/profile", DownProfile()),  # 503
        handler.process("GET", good, b"", None, "/verdicts", None, dict),  # 200
        handler.process("PUT", good, b"", None, "/verdicts", None, dict),  # 405
        handler.process("GET", good, b"", None, "/verdicts", None, down),  # 503
    ]
    statuses = [r["statusCode"] for r in answers]
    assert statuses == [403, 204, 405, 401, 400, 202, 429, 503, 200, 409, 503, 200, 405, 503]
    assert all(r["headers"]["Cache-Control"] == "no-store" for r in answers)


def test_preflight_and_cors():
    resp, _ = call("OPTIONS", token=None)
    assert resp["statusCode"] == 204
    assert resp["headers"]["Access-Control-Allow-Origin"] == ORIGIN
    assert "Authorization" in resp["headers"]["Access-Control-Allow-Headers"]
    assert call(origin="https://evil.example")[0]["statusCode"] == 403
    assert call(origin=None)[0]["statusCode"] == 403
    assert call("GET")[0]["statusCode"] == 405


def test_allowed_origin_is_configurable(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGIN", "https://other.example")
    assert call("OPTIONS", origin="https://other.example")[0]["statusCode"] == 204
    assert call("OPTIONS")[0]["statusCode"] == 403


def test_auth(monkeypatch):
    body = {"items": [{"kind": "like", "artist_keys": ["asna"]}]}
    assert call(body=body, token="wrong")[0]["statusCode"] == 401
    assert call(body=body, token=None)[0]["statusCode"] == 401
    monkeypatch.setenv("FEEDBACK_TOKEN_SHA256", "")
    assert call(body=body)[0]["statusCode"] == 401


def test_stores_one_row_per_artist_key():
    body = {
        "items": [
            {"kind": "like", "concert_id": CID, "artist_keys": ["asna", "boris", "asna"]},
            {"kind": "wrong", "concert_id": CID, "artist_keys": []},
            {"kind": "unlike", "concert_id": None, "artist_keys": ["sunnco"]},
        ]
    }
    resp, rows = call(body=body)
    assert resp["statusCode"] == 202 and json.loads(resp["body"]) == {"stored": 4}
    assert rows == [
        (CID, "asna", "like"),
        (CID, "boris", "like"),
        (CID, None, "wrong"),
        (None, "sunnco", "unlike"),
    ]


@pytest.mark.parametrize(
    "body,reason",
    [
        (b"x" * 5000, "body too large"),
        (b"{not json", "invalid json"),
        ({"items": [], "extra": 1}, "expected {items}"),
        ({"items": []}, "items: 1 to 50"),
        ({"items": [{"kind": "like", "artist_keys": ["a"]}] * 51}, "items: 1 to 50"),
        ({"items": [{"kind": "love", "artist_keys": ["a"]}]}, "bad kind"),
        ({"items": [{"kind": "like", "artist_keys": ["a"], "x": 1}]}, "unexpected item field"),
        ({"items": [{"kind": "like", "concert_id": "DROP TABLE"}]}, "bad concert_id"),
        ({"items": [{"kind": "like", "artist_keys": ["Bad Key"]}]}, "bad artist key"),
        ({"items": [{"kind": "like", "artist_keys": ["a" * 101]}]}, "bad artist key"),
        ({"items": [{"kind": "like", "artist_keys": ["a"] * 13}]}, "artist_keys: at most 12"),
        ({"items": [{"kind": "like"}]}, "concert_id or artist_keys required"),
    ],
)
def test_rejects_bad_bodies(body, reason):
    resp, rows = call(body=body)
    assert resp["statusCode"] == 400 and json.loads(resp["body"]) == {"error": reason}
    assert rows == []


def test_db_error_is_503_without_details(caplog):
    def broken(rows):
        raise RuntimeError("password=hunter2")

    resp, _ = call(body={"items": [{"kind": "like", "artist_keys": ["a"]}]}, store=broken)
    assert resp["statusCode"] == 503 and "hunter2" not in resp["body"] + caplog.text
    assert "s3cret" not in caplog.text


def test_handle_decodes_scaleway_events(monkeypatch):
    seen = {}
    monkeypatch.setattr(handler, "pg_store", lambda rows: seen.setdefault("n", len(rows)))
    body = json.dumps({"items": [{"kind": "dislike", "concert_id": CID}]})
    event = {
        "httpMethod": "POST",
        "headers": {"origin": ORIGIN, "authorization": "Bearer s3cret"},
        "body": base64.b64encode(body.encode()).decode(),
        "isBase64Encoded": True,
    }
    assert handler.handle(event, None)["statusCode"] == 202 and seen["n"] == 1
    event["body"] = "!!not base64!!"
    assert handler.handle(event, None)["statusCode"] == 400


@pytest.mark.skipif(
    urlsplit(os.environ.get("TEST_DATABASE_URL", "")).hostname not in ("localhost", "127.0.0.1"),
    reason="needs a local TEST_DATABASE_URL (the test empties the feedback table)",
)
def test_pg_store_on_real_postgres(monkeypatch):
    import psycopg

    from nightcrawler.store.migrate import migrate

    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        migrate(conn)
        conn.execute("DELETE FROM feedback")
    monkeypatch.setenv("DATABASE_URL", url)
    assert handler.pg_store([(CID, "asna", "like"), (CID, None, "wrong")]) == 2
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM feedback").fetchone()[0] == 2
    # rate limit: the last minute already holds RATE_LIMIT - 1 rows, two more are refused
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM feedback")
        conn.execute(
            "INSERT INTO feedback (artist_key, kind) "
            "SELECT 'asna', 'like' FROM generate_series(1, %s)",
            (handler.RATE_LIMIT - 1,),
        )
    with pytest.raises(handler.RateLimited):
        handler.pg_store([(CID, "a", "like"), (CID, "b", "like")])
    assert handler.pg_store([(CID, "a", "like")]) == 1
    with psycopg.connect(url) as conn:
        count = conn.execute("SELECT count(*) FROM feedback").fetchone()[0]
        assert count == handler.RATE_LIMIT


def test_rate_limited_is_429():
    def limited(rows):
        raise handler.RateLimited

    resp, _ = call(body={"items": [{"kind": "like", "artist_keys": ["a"]}]}, store=limited)
    assert resp["statusCode"] == 429


# ---------------------------------------------------------------- profile (WIP-46)

PROFILE = {
    "seeds": [{"name": "Asna", "tags": ["drone"]}, {"name": "Boris", "tags": None}],
    "liked": ["asna"],
    "hidden": [CID],
}


class FakeProfile:
    def __init__(self, row=None):
        self.row = row

    def get(self):
        return self.row

    def put(self, data, base):
        if (self.row["version"] if self.row else 0) != base:
            return False, self.row
        data = handler.keep_taste_text(data, self.row["data"] if self.row else None)
        self.row = {"data": data, "version": base + 1, "updated_at": "t"}
        return True, {"version": base + 1}


def pcall(method, body=None, path="/profile", profile=None, token="s3cret"):
    headers = {"Origin": ORIGIN, "Authorization": f"Bearer {token}"}
    raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
    resp = handler.process(method, headers, raw, None, path, profile or FakeProfile())
    return resp["statusCode"], json.loads(resp["body"] or "{}")


def test_profile_get_put_and_conflict():
    fake = FakeProfile()
    assert pcall("GET", profile=fake) == (200, {"data": None, "version": 0})
    assert pcall("PUT", {"data": PROFILE, "base_version": 1}, profile=fake) == (
        409,
        {"data": None, "version": 0},
    )
    assert pcall("PUT", {"data": PROFILE, "base_version": 0}, profile=fake) == (200, {"version": 1})
    status, got = pcall("GET", path="profile", profile=fake)  # with or without the slash
    assert status == 200 and got["version"] == 1 and got["data"]["liked"] == ["asna"]
    assert got["data"]["dislikedNames"] == [] and got["data"]["seeds"][1]["tags"] is None
    status, got = pcall("PUT", {"data": {}, "base_version": 0}, profile=fake)  # stale base
    assert status == 409 and got["version"] == 1 and got["data"]["hidden"] == [CID]
    assert pcall("PUT", {"data": {}, "base_version": 1}, profile=fake) == (200, {"version": 2})
    assert pcall("POST", {"data": {}, "base_version": 2}, profile=fake)[0] == 405
    assert pcall("GET", token="wrong", profile=fake)[0] == 401
    preflight = handler.process("OPTIONS", {"Origin": ORIGIN}, b"", None, "/profile")
    assert "PUT" in preflight["headers"]["Access-Control-Allow-Methods"]


@pytest.mark.parametrize(
    "body,reason",
    [
        (b"x" * 70000, "body too large"),
        ({"data": {}}, "expected {data, base_version}"),
        ({"data": {}, "base_version": -1}, "bad base_version"),
        ({"data": {}, "base_version": True}, "bad base_version"),
        ({"data": {"sort": "me"}, "base_version": 0}, "unexpected profile field"),
        ({"data": {"seeds": [{"name": "a"}] * 201}, "base_version": 0}, "seeds: at most 200"),
        ({"data": {"seeds": [{"name": "a" * 61}]}, "base_version": 0}, "bad seed name"),
        ({"data": {"seeds": [{"name": "a", "x": 1}]}, "base_version": 0}, "bad seed"),
        (
            {"data": {"seeds": [{"name": "a", "tags": ["t"] * 13}]}, "base_version": 0},
            "bad seed tags",
        ),
        ({"data": {"liked": ["Bad Key"]}, "base_version": 0}, "bad liked item"),
        ({"data": {"hidden": ["asna"]}, "base_version": 0}, "bad hidden item"),
        ({"data": {"wrong": ["a"] * 2001}, "base_version": 0}, "wrong: at most 2000"),
        ({"data": {"hidden": ["0" * 12] * 501}, "base_version": 0}, "hidden: at most 500"),
        (
            {"data": {"likedConcerts": ["0" * 12] * 501}, "base_version": 0},
            "likedConcerts: at most 500",
        ),
    ],
)
def test_profile_rejects_bad_bodies(body, reason):
    assert pcall("PUT", body) == (400, {"error": reason})


def test_profile_accepts_500_concert_ids_per_list():
    # WIP-59: the page keeps the 500 most recent ids; the function accepts exactly that
    ids = [f"{i:012x}" for i in range(handler.MAX_IDS)]
    data, _ = handler.validate_profile(
        json.dumps({"data": {"hidden": ids, "likedConcerts": ids}, "base_version": 0}).encode()
    )
    assert data["hidden"] == ids and data["likedConcerts"] == ids


# ---------------------------------------------------------------- written taste (WIP-73)


@pytest.mark.parametrize(
    "data,reason",
    [
        ({"taste_text": "a" * 4001}, "taste_text: a string of at most 4000 characters"),
        ({"taste_text": 3}, "taste_text: a string of at most 4000 characters"),
        ({"taste_text": None}, "taste_text: a string of at most 4000 characters"),
        ({"taste_text": "ok\x00"}, "taste_text: a string of at most 4000 characters"),
        ({"taste_text": "ok\ud800"}, "taste_text: not valid unicode"),
        ({"taste_text_at": -1}, "bad taste_text_at"),
        ({"taste_text_at": "1"}, "bad taste_text_at"),
        ({"taste_text_at": True}, "bad taste_text_at"),
        ({"taste_text_at": 2**53}, "bad taste_text_at"),
    ],
)
def test_profile_rejects_bad_taste_text(data, reason):
    assert pcall("PUT", {"data": data, "base_version": 0}) == (400, {"error": reason})


def test_profile_taste_text_round_trip_and_defaults():
    fake = FakeProfile()
    head = "Drone et noise ; jazz seulement s'il croise autre chose. 🎷"
    text = head + "é" * (4000 - len(head))  # exactly the cap, non-ASCII included
    body = {"data": {**PROFILE, "taste_text": text, "taste_text_at": 1_760_000_000_000}}
    assert pcall("PUT", {**body, "base_version": 0}, profile=fake) == (200, {"version": 1})
    status, got = pcall("GET", profile=fake)
    assert status == 200
    assert got["data"]["taste_text"] == text
    assert got["data"]["taste_text_at"] == 1_760_000_000_000


def test_profile_save_without_taste_text_keeps_the_stored_text():
    # a page older than WIP-73 PUTs profiles without the field: the stored text stays
    fake = FakeProfile()
    body = {"data": {**PROFILE, "taste_text": "drone", "taste_text_at": 50}, "base_version": 0}
    assert pcall("PUT", body, profile=fake) == (200, {"version": 1})
    assert pcall("PUT", {"data": {"liked": []}, "base_version": 1}, profile=fake)[0] == 200
    data = pcall("GET", profile=fake)[1]["data"]
    assert data["liked"] == [] and (data["taste_text"], data["taste_text_at"]) == ("drone", 50)
    # an older edit does not replace it either
    old = {"data": {"taste_text": "", "taste_text_at": 49}, "base_version": 2}
    assert pcall("PUT", old, profile=fake)[0] == 200
    assert pcall("GET", profile=fake)[1]["data"]["taste_text"] == "drone"


def test_profile_explicit_empty_taste_text_with_newer_time_clears_it():
    fake = FakeProfile()
    body = {"data": {"taste_text": "drone", "taste_text_at": 50}, "base_version": 0}
    assert pcall("PUT", body, profile=fake)[0] == 200
    clear = {"data": {"taste_text": "", "taste_text_at": 51}, "base_version": 1}
    assert pcall("PUT", clear, profile=fake) == (200, {"version": 2})
    data = pcall("GET", profile=fake)[1]["data"]
    assert (data["taste_text"], data["taste_text_at"]) == ("", 51)


def test_profile_worst_case_taste_text_fits_the_body_limit():
    # 4,000 characters that JSON escapes to 6 bytes each, plus both id lists at their cap
    ids = [f"{i:012x}" for i in range(handler.MAX_IDS)]
    data = {"taste_text": "\x01" * 4000, "taste_text_at": 2**53 - 1, "hidden": ids}
    raw = json.dumps({"data": {**data, "likedConcerts": ids}, "base_version": 0}).encode()
    assert len(raw) < handler.MAX_PROFILE_BODY
    assert handler.validate_profile(raw)[0]["taste_text"] == "\x01" * 4000
    # the body limit still applies first
    big = json.dumps({"data": {"taste_text": "a" * 70000}, "base_version": 0}).encode()
    assert pcall("PUT", big) == (400, {"error": "body too large"})


def test_taste_text_is_never_logged(caplog):
    class Down(FakeProfile):
        def put(self, data, base):
            raise RuntimeError(data["taste_text"])

    secret = "texte personnel tres reconnaissable"
    with caplog.at_level(logging.DEBUG):
        body = {"data": {"taste_text": secret}, "base_version": 0}
        status, got = pcall("PUT", body, profile=Down())
    assert status == 503 and secret not in json.dumps(got)
    assert secret not in caplog.text


def test_profile_store_errors():
    class Down:
        def get(self):
            raise RuntimeError("password=hunter2")

        put = get

    class Busy(FakeProfile):
        def put(self, data, base):
            raise handler.RateLimited

    status, got = pcall("GET", profile=Down())
    assert status == 503 and "hunter2" not in json.dumps(got)
    assert pcall("PUT", {"data": {}, "base_version": 0}, profile=Busy())[0] == 429


def test_handle_routes_by_path(monkeypatch):
    seen = {}

    def fake_process(method, headers, raw, store, path):
        seen.update(method=method, path=path)
        return {}

    monkeypatch.setattr(handler, "process", fake_process)
    handler.handle({"method": "GET", "rawPath": "/profile", "headers": {}}, None)
    assert seen == {"method": "GET", "path": "/profile"}
    handler.handle({"httpMethod": "PUT", "path": "/profile", "body": "{}"}, None)
    assert seen == {"method": "PUT", "path": "/profile"}


@pytest.mark.skipif(
    urlsplit(os.environ.get("TEST_DATABASE_URL", "")).hostname not in ("localhost", "127.0.0.1"),
    reason="needs a local TEST_DATABASE_URL (the test empties the profile tables)",
)
def test_pg_profile_on_real_postgres(monkeypatch):
    import psycopg

    from nightcrawler.store.migrate import migrate

    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        migrate(conn)
        conn.execute("DELETE FROM profile; DELETE FROM profile_writes")
    monkeypatch.setenv("DATABASE_URL", url)
    pg = handler.PgProfile()
    assert pg.get() is None
    assert pg.put(PROFILE, 1) == (False, None)
    assert pg.put(PROFILE, 0) == (True, {"version": 1})
    assert pg.put({"liked": []}, 0) == (False, {"data": PROFILE, "version": 1})
    assert pg.put({"liked": []}, 1) == (True, {"version": 2})
    # a concurrent first upload: the other transaction commits while this INSERT waits on it
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM profile")
    other = psycopg.connect(url)
    other.execute("INSERT INTO profile (data, version) VALUES ('{}', 7)")
    timer = threading.Timer(0.5, other.commit)
    timer.start()
    assert pg.put({"liked": []}, 0) == (False, {"data": {}, "version": 7})
    timer.join()
    other.close()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("UPDATE profile SET data = %s, version = 2", (json.dumps({"liked": []}),))
    row = pg.get()
    assert row["data"] == {"liked": []} and row["version"] == 2 and row["updated_at"]
    taste = {"liked": [], "taste_text": "drone", "taste_text_at": 50}
    assert pg.put(taste, 2) == (True, {"version": 3})
    assert pg.put({"liked": []}, 3) == (True, {"version": 4})  # field absent: text kept
    assert pg.get()["data"]["taste_text"] == "drone"
    assert pg.put({**taste, "taste_text": "", "taste_text_at": 51}, 4) == (True, {"version": 5})
    assert pg.get()["data"]["taste_text"] == ""
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("UPDATE profile SET version = 2")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO profile_writes SELECT now() FROM generate_series(1, %s)",
            (handler.PROFILE_RATE_LIMIT,),
        )
    with pytest.raises(handler.RateLimited):
        pg.put({}, 2)
    assert pg.get()["version"] == 2


# ---------------------------------------------------------------- verdicts (WIP-85)

VERDICT = {"section": "pour_toi", "verdict": "for_you", "confidence": 72, "reason": "drone"}


def vcall(method="GET", path="/verdicts", read=None, token="s3cret", origin=ORIGIN):
    headers = {"Origin": origin} if origin else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    calls = []

    def fake():
        calls.append(1)
        return {CID: VERDICT}

    resp = handler.process(method, headers, b"", None, path, None, read or fake)
    return resp, calls


def test_verdicts_get_with_or_without_slash():
    for path in ("/verdicts", "verdicts", "/feedback/verdicts/"):
        resp, calls = vcall(path=path)
        assert resp["statusCode"] == 200 and calls == [1]
        body = json.loads(resp["body"])
        assert body["verdicts"] == {CID: VERDICT}
        assert body["generated_at"].endswith("+00:00")
        assert resp["headers"]["Cache-Control"] == "no-store"
        assert resp["headers"]["Access-Control-Allow-Origin"] == ORIGIN


def test_verdicts_empty_is_200_not_404():
    resp, _ = vcall(read=lambda: {})
    assert resp["statusCode"] == 200 and json.loads(resp["body"])["verdicts"] == {}


def test_verdicts_auth_and_origin(monkeypatch):
    resp, calls = vcall(token="wrong")
    assert resp["statusCode"] == 401 and calls == []  # nothing read without the key
    assert json.loads(resp["body"]) == {"error": "unauthorized"}
    assert vcall(token=None)[0]["statusCode"] == 401
    assert vcall(origin="https://evil.example")[0]["statusCode"] == 403
    assert vcall(origin=None)[0]["statusCode"] == 403
    monkeypatch.setenv("FEEDBACK_TOKEN_SHA256", "")
    assert vcall()[0]["statusCode"] == 401
    # the preflight already allows GET on every path
    preflight = handler.process("OPTIONS", {"Origin": ORIGIN}, b"", None, "/verdicts")
    assert preflight["statusCode"] == 204
    assert "GET" in preflight["headers"]["Access-Control-Allow-Methods"]


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_verdicts_other_methods_are_405_without_reading(method):
    resp, calls = vcall(method)
    assert resp["statusCode"] == 405 and calls == []
    assert resp["headers"]["Cache-Control"] == "no-store"


def test_verdicts_reader_error_is_503_and_logs_no_content(caplog):
    def down():
        raise RuntimeError("password=hunter2 du free jazz comme tu aimes")

    with caplog.at_level(logging.DEBUG):
        resp, _ = vcall(read=down)
    assert resp["statusCode"] == 503 and json.loads(resp["body"]) == {
        "error": "storage unavailable"
    }
    assert resp["headers"]["Cache-Control"] == "no-store"
    assert "RuntimeError" in caplog.text
    assert "hunter2" not in caplog.text + resp["body"] and "jazz" not in caplog.text


def test_verdicts_logs_a_count_only(caplog):
    with caplog.at_level(logging.DEBUG):
        resp, _ = vcall()
    assert resp["statusCode"] == 200
    assert "verdicts served: 1" in caplog.text
    assert "drone" not in caplog.text and CID not in caplog.text


@pytest.mark.skipif(
    urlsplit(os.environ.get("TEST_DATABASE_URL", "")).hostname not in ("localhost", "127.0.0.1"),
    reason="needs a local TEST_DATABASE_URL (the test empties the verdicts table)",
)
def test_pg_verdicts_on_real_postgres(monkeypatch):
    import psycopg

    from nightcrawler.store.migrate import migrate

    url = os.environ["TEST_DATABASE_URL"]
    ids = {"later": "aaaaaaaaaaa1", "23h_ago": "aaaaaaaaaaa2", "25h_ago": "aaaaaaaaaaa3"}
    starts = {"later": "2 hours", "23h_ago": "-23 hours", "25h_ago": "-25 hours"}
    with psycopg.connect(url, autocommit=True) as conn:
        migrate(conn)
        conn.execute("DELETE FROM verdicts")
        conn.execute("DELETE FROM concerts WHERE id = ANY(%s)", (list(ids.values()),))
        for key, cid in ids.items():
            conn.execute("INSERT INTO concerts (id, data) VALUES (%s, '{}')", (cid,))
            conn.execute(
                "INSERT INTO verdicts (concert_id, input_hash, verdict, confidence, reason, "
                "section, model, starts_at) VALUES (%s, 'h', 'must_see', 91, %s, "
                "'ne_pas_rater', 'm', now() + %s::interval)",
                (cid, f"reason {key}", starts[key]),
            )
    monkeypatch.setenv("DATABASE_URL", url)
    got = handler.pg_verdicts()
    assert got == {
        ids["later"]: {
            "section": "ne_pas_rater",
            "verdict": "must_see",
            "confidence": 91,
            "reason": "reason later",
        },
        ids["23h_ago"]: {
            "section": "ne_pas_rater",
            "verdict": "must_see",
            "confidence": 91,
            "reason": "reason 23h_ago",
        },
    }
    assert isinstance(got[ids["later"]]["confidence"], int)
    resp = handler.process(
        "GET",
        {"Origin": ORIGIN, "Authorization": "Bearer s3cret"},
        b"",
        None,
        "/verdicts",
    )  # the default reader is pg_verdicts
    assert resp["statusCode"] == 200 and set(json.loads(resp["body"])["verdicts"]) == {
        ids["later"],
        ids["23h_ago"],
    }
    # read-only for real: inside pg_verdicts' transaction, just before its SELECT, the session
    # reports transaction_read_only = on and the 10 s timeout, and a write is refused
    real_connect, checks = psycopg.connect, {}

    class Spy:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *exc):
            return self.conn.__exit__(*exc)

        def transaction(self):
            return self.conn.transaction()

        def execute(self, sql, *args):
            if "FROM verdicts" in sql:
                checks["read_only"] = self.conn.execute("SHOW transaction_read_only").fetchone()[0]
                checks["timeout"] = self.conn.execute("SHOW statement_timeout").fetchone()[0]
                try:
                    with self.conn.transaction():  # a savepoint: the SELECT still runs after
                        self.conn.execute("DELETE FROM verdicts")
                except psycopg.errors.ReadOnlySqlTransaction:
                    checks["write"] = "refused"
            return self.conn.execute(sql, *args)

    with monkeypatch.context() as m:
        m.setattr(handler.psycopg, "connect", lambda u, **kw: Spy(real_connect(u, **kw)))
        assert set(handler.pg_verdicts()) == {ids["later"], ids["23h_ago"]}
    assert checks == {"read_only": "on", "timeout": "10s", "write": "refused"}
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM verdicts").fetchone()[0] == 3
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM concerts WHERE id = ANY(%s)", (list(ids.values()),))
    assert handler.pg_verdicts() == {}  # ON DELETE CASCADE; empty is {}


def test_pg_verdicts_reads_in_a_read_only_transaction(monkeypatch):
    seen = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def transaction(self):
            return self

        def execute(self, sql, *args):
            seen.append(sql)
            return self

        def fetchall(self):
            return [(CID, "pour_toi", "for_you", 72, "drone")]

    def connect(url, **kwargs):
        seen.append(kwargs)
        return Conn()

    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/db")
    monkeypatch.setattr(handler.psycopg, "connect", connect)
    assert handler.pg_verdicts() == {CID: VERDICT}
    assert seen[0] == {"connect_timeout": 15, "autocommit": True}
    assert seen[1] == "SET TRANSACTION READ ONLY"
    assert seen[2].startswith("SET LOCAL statement_timeout")
    assert "starts_at >= now() - interval '1 day'" in seen[3]


# ---------------------------------------------------------------- packaging and deploy

CREDS = provision.Credentials("SCWACCESS", "secret/key", "proj-1")
FN = f"{provision.API}{dfn.BASE}"


def test_build_zip_has_handler_and_package(tmp_path):
    def fake_install(target):
        (target / "psycopg").mkdir(parents=True)
        (target / "psycopg" / "__init__.py").write_text("")
        (target / "psycopg" / "__pycache__").mkdir()
        (target / "psycopg" / "__pycache__" / "x.pyc").write_text("")

    archive = dfn.build_zip(tmp_path / "f.zip", install=fake_install)
    names = zipfile.ZipFile(archive).namelist()
    assert names == ["handler.py", "package/psycopg/__init__.py"]


@respx.mock
def test_deploy_creates_uploads_and_waits(tmp_path, monkeypatch):
    monkeypatch.setattr(dfn.time, "sleep", lambda s: None)
    respx.get(f"{FN}/namespaces").respond(json={"namespaces": []})
    ns = respx.post(f"{FN}/namespaces").respond(json={"id": "ns1", "status": "pending"})
    respx.get(f"{FN}/namespaces/ns1").respond(json={"id": "ns1", "status": "ready"})
    respx.get(f"{FN}/functions").respond(json={"functions": []})
    create = respx.post(f"{FN}/functions").respond(json={"id": "f1", "status": "created"})
    respx.get(f"{FN}/functions/f1/upload-url").respond(
        json={"url": "https://s3.example/up", "headers": {"x-amz-meta": ["1"]}}
    )
    put = respx.put("https://s3.example/up").respond(200)
    deploy = respx.post(f"{FN}/functions/f1/deploy").respond(json={"id": "f1"})
    respx.get(f"{FN}/functions/f1").mock(
        side_effect=[
            httpx.Response(200, json={"id": "f1", "status": "created"}),
            httpx.Response(200, json={"id": "f1", "status": "pending"}),
            httpx.Response(200, json={"id": "f1", "status": "ready", "domain_name": "d.fn"}),
        ]
    )
    archive = tmp_path / "f.zip"
    archive.write_bytes(b"zipdata")
    settings = dfn.function_settings(ORIGIN, "postgresql://u:p@h/db", "abc")
    fn = dfn.deploy(CREDS, settings, archive)
    assert fn["domain_name"] == "d.fn"
    assert json.loads(ns.calls[0].request.content)["project_id"] == "proj-1"
    sent = json.loads(create.calls[0].request.content)
    assert sent["runtime"] == "python312" and sent["handler"] == "handler.handle"
    assert sent["min_scale"] == 0 and sent["max_scale"] == 1 and sent["memory_limit"] == 128
    assert sent["privacy"] == "public" and sent["environment_variables"] == {
        "ALLOWED_ORIGIN": ORIGIN
    }
    assert {s["key"] for s in sent["secret_environment_variables"]} == {
        "DATABASE_URL",
        "FEEDBACK_TOKEN_SHA256",
    }
    assert put.calls[0].request.content == b"zipdata"
    assert put.calls[0].request.headers["x-amz-meta"] == "1"
    assert deploy.called


@respx.mock
def test_deploy_reuses_and_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(dfn.time, "sleep", lambda s: None)
    respx.get(f"{FN}/namespaces").respond(
        json={"namespaces": [{"id": "ns1", "name": "nightcrawler", "status": "ready"}]}
    )
    respx.get(f"{FN}/namespaces/ns1").respond(json={"id": "ns1", "status": "ready"})
    respx.get(f"{FN}/functions").respond(
        json={"functions": [{"id": "f1", "name": "feedback", "status": "ready"}]}
    )
    create = respx.post(f"{FN}/functions").respond(500)
    patch = respx.patch(f"{FN}/functions/f1").respond(json={"id": "f1", "status": "pending"})
    # a previous failed build is replaced: upload + deploy again, then report the new failure
    respx.get(f"{FN}/functions/f1").respond(
        json={"id": "f1", "status": "error", "error_message": "build failed"}
    )
    respx.get(f"{FN}/functions/f1/upload-url").respond(json={"url": "https://s3.test/up"})
    put = respx.put("https://s3.test/up").respond(200)
    deploy = respx.post(f"{FN}/functions/f1/deploy").respond(json={"id": "f1"})
    archive = tmp_path / "f.zip"
    archive.write_bytes(b"z")
    with pytest.raises(provision.ProvisionError, match="build failed"):
        dfn.deploy(CREDS, dfn.function_settings(ORIGIN, "u", "h"), archive)
    assert patch.called and not create.called and put.called and deploy.called


@respx.mock
def test_smoke_test(monkeypatch):
    monkeypatch.setattr(dfn.time, "sleep", lambda s: None)
    route = respx.options("https://d.fn").mock(
        side_effect=[httpx.ConnectError("cold"), httpx.Response(204)]
    )
    post = respx.post("https://d.fn").respond(401)
    profile = respx.get("https://d.fn/profile").respond(401)
    verdicts = respx.get("https://d.fn/verdicts").respond(401)
    respx.get("https://d.fn/").respond(405)
    assert dfn.smoke_test("https://d.fn", ORIGIN) == (204, 401, 401, 401, 405)
    for sub in (profile, verdicts):
        assert (
            sub.calls[0].request.headers["Authorization"]
            == post.calls[0].request.headers["Authorization"]
        )
        assert sub.calls[0].request.headers["Origin"] == ORIGIN
    assert route.calls[1].request.headers["Origin"] == ORIGIN
    sent = post.calls[0].request.headers["Authorization"]
    assert sent.startswith("Bearer ") and len(sent) > 20


def test_cli_skips_without_token(monkeypatch, capsys):
    monkeypatch.delenv("FEEDBACK_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert cli.main(["deploy-feedback"]) == 0
    assert "skipped: FEEDBACK_TOKEN not set" in capsys.readouterr().out


def test_cli_deploys_with_hashed_token_and_masked_url(monkeypatch, capsys):
    monkeypatch.setenv("FEEDBACK_TOKEN", "s3cret")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for k, v in (("SCW_ACCESS_KEY", "a"), ("SCW_SECRET_KEY", "b"), ("SCW_DEFAULT_PROJECT_ID", "p")):
        monkeypatch.setenv(k, v)
    got = {}
    monkeypatch.setattr(provision, "ensure", lambda creds: ({}, "postgresql://u:pw@h/db"))
    monkeypatch.setattr(dfn, "build_zip", lambda dest: dest)

    def fake_deploy(creds, settings, archive):
        got.update(settings)
        return {"status": "ready", "domain_name": "d.fn", "runtime": "python312"}

    monkeypatch.setattr(dfn, "deploy", fake_deploy)
    monkeypatch.setattr(dfn, "smoke_test", lambda url, origin: (204, 401, 401, 401, 405))
    assert cli.main(["deploy-feedback"]) == 0
    out = capsys.readouterr().out
    assert "::add-mask::postgresql://u:pw@h/db" in out and "https://d.fn" in out
    assert "s3cret" not in out
    secrets = {s["key"]: s["value"] for s in got["secret_environment_variables"]}
    assert secrets["FEEDBACK_TOKEN_SHA256"] == hashlib.sha256(b"s3cret").hexdigest()


def test_cli_fails_when_wrong_token_is_accepted(monkeypatch, capsys):
    monkeypatch.setenv("FEEDBACK_TOKEN", "s3cret")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for k, v in (("SCW_ACCESS_KEY", "a"), ("SCW_SECRET_KEY", "b"), ("SCW_DEFAULT_PROJECT_ID", "p")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(provision, "ensure", lambda creds: ({}, "postgresql://u:pw@h/db"))
    monkeypatch.setattr(dfn, "build_zip", lambda dest: dest)
    monkeypatch.setattr(dfn, "deploy", lambda c, s, a: {"domain_name": "d.fn"})
    for statuses in (
        (204, 202, 401, 401, 405),  # wrong token accepted
        (204, 401, 404, 401, 405),  # /profile not routed
        (204, 401, 401, 404, 405),  # /verdicts not routed (WIP-85)
        (204, 401, 401, 200, 405),  # /verdicts served without the key
        (204, 401, 401, 401, 401),
    ):
        monkeypatch.setattr(dfn, "smoke_test", lambda url, origin, s=statuses: s)
        assert cli.main(["deploy-feedback"]) == 1  # wrong token accepted, or sub-path not routed
        assert "::error::" in capsys.readouterr().out


def test_cli_http_error_reports_type_only(monkeypatch, capsys):
    monkeypatch.setenv("FEEDBACK_TOKEN", "s3cret")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for k, v in (("SCW_ACCESS_KEY", "a"), ("SCW_SECRET_KEY", "b"), ("SCW_DEFAULT_PROJECT_ID", "p")):
        monkeypatch.setenv(k, v)

    def boom(creds):
        raise httpx.ConnectError("https://s3.example/up?X-Amz-Signature=secret")

    monkeypatch.setattr(provision, "ensure", boom)
    assert cli.main(["deploy-feedback"]) == 1
    out = capsys.readouterr().out
    assert "ConnectError" in out and "Signature" not in out


def test_upload_headers_are_not_duplicated():
    from nightcrawler.store.deploy_function import upload_headers

    h = upload_headers(
        {"headers": {"content-type": ["application/octet-stream"], "x-amz-acl": "private"}}
    )
    assert h.get_list("content-type") == ["application/octet-stream"]
    assert h["x-amz-acl"] == "private"
    assert upload_headers({})["content-type"] == "application/octet-stream"


def test_wait_can_accept_a_previous_error(monkeypatch):
    import httpx as _httpx
    import respx as _respx

    from nightcrawler.store import deploy_function as d

    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    with _respx.mock:
        _respx.get("https://x.test/f").mock(
            side_effect=[
                _httpx.Response(200, json={"status": "pending"}),
                _httpx.Response(200, json={"status": "error", "error_message": "old build"}),
            ]
        )
        with _httpx.Client() as c:
            assert (
                d._wait(c, "https://x.test/f", "function", 60, ok=("ready", "error"), fail=())[
                    "status"
                ]
                == "error"
            )
