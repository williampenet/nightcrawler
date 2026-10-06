import base64
import hashlib
import importlib.util
import json
import os
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
        self.row = {"data": data, "version": base + 1, "updated_at": "t"}
        return True, {"version": base + 1}


def pcall(method, body=None, path="/profile", profile=None, token="s3cret"):
    headers = {"Origin": ORIGIN, "Authorization": f"Bearer {token}"}
    raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
    resp = handler.process(method, headers, raw, None, path, profile or FakeProfile())
    return resp["statusCode"], json.loads(resp["body"] or "{}")


def test_profile_get_put_and_conflict():
    fake = FakeProfile()
    assert pcall("GET", profile=fake) == (404, {})
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
    ],
)
def test_profile_rejects_bad_bodies(body, reason):
    assert pcall("PUT", body) == (400, {"error": reason})


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
    row = pg.get()
    assert row["data"] == {"liked": []} and row["version"] == 2 and row["updated_at"]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO profile_writes SELECT now() FROM generate_series(1, %s)",
            (handler.PROFILE_RATE_LIMIT,),
        )
    with pytest.raises(handler.RateLimited):
        pg.put({}, 2)
    assert pg.get()["version"] == 2


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
    assert dfn.smoke_test("https://d.fn", ORIGIN) == (204, 401)
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
    monkeypatch.setattr(dfn, "smoke_test", lambda url, origin: (204, 401))
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
    monkeypatch.setattr(dfn, "smoke_test", lambda url, origin: (204, 202))
    assert cli.main(["deploy-feedback"]) == 1
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
