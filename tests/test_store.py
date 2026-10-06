import os

import pytest
import respx

from nightcrawler.store import provision
from nightcrawler.store.migrate import migrate, migrations

CREDS = provision.Credentials("SCWACCESS", "secret/key", "proj-1")
DBS = f"{provision.API}/serverless-sqldb/v1alpha1/regions/fr-par/databases"


def test_connection_url_inserts_iam_credentials_and_tls():
    url = provision.connection_url(
        "postgres://abc.pg.sdb.fr-par.scw.cloud:5432/nightcrawler", "user-id", "s/cret:"
    )
    assert url == (
        "postgresql://user-id:s%2Fcret%3A@abc.pg.sdb.fr-par.scw.cloud:5432/"
        "nightcrawler?sslmode=require"
    )


@respx.mock
def test_ensure_reuses_or_creates_and_waits(monkeypatch):
    monkeypatch.setattr(provision.time, "sleep", lambda s: None)
    respx.get(f"{provision.API}/iam/v1alpha1/api-keys/SCWACCESS").respond(
        json={"user_id": "user-1", "application_id": None}
    )
    respx.get(DBS).respond(json={"databases": []})
    create = respx.post(DBS).respond(
        json={"id": "db1", "name": "nightcrawler", "status": "creating"}
    )
    respx.get(f"{DBS}/db1").respond(
        json={
            "id": "db1",
            "name": "nightcrawler",
            "status": "ready",
            "endpoint": "postgres://h.example:5432/nightcrawler",
        }
    )
    db, url = provision.ensure(CREDS)
    body = create.calls[0].request
    assert body.headers["X-Auth-Token"] == "secret/key"
    assert b'"cpu_min":0' in body.content.replace(b" ", b"")
    assert db["status"] == "ready" and url.startswith("postgresql://user-1:secret%2Fkey@h.example")


@respx.mock
def test_ensure_fails_clearly():
    respx.get(f"{provision.API}/iam/v1alpha1/api-keys/SCWACCESS").respond(403, text="denied")
    with pytest.raises(provision.ProvisionError, match="HTTP 403"):
        provision.ensure(CREDS)


@respx.mock
def test_lookup_only_never_creates():
    respx.get(f"{provision.API}/iam/v1alpha1/api-keys/SCWACCESS").respond(json={"user_id": "u"})
    respx.get(DBS).respond(json={"databases": []})
    create = respx.post(DBS).respond(json={})
    with pytest.raises(provision.ProvisionError, match="not found"):
        provision.ensure(CREDS, create=False)
    assert not create.called


@respx.mock
def test_existing_database_is_reused():
    respx.get(f"{provision.API}/iam/v1alpha1/api-keys/SCWACCESS").respond(json={"user_id": "u"})
    listing = respx.get(DBS).respond(
        json={
            "databases": [
                {
                    "id": "x",
                    "name": "nightcrawler-old",
                    "status": "ready",
                    "endpoint": "postgres://o/db",
                },
                {
                    "id": "db1",
                    "name": "nightcrawler",
                    "status": "ready",
                    "endpoint": "postgres://h/db",
                },
            ]
        }
    )
    create = respx.post(DBS).respond(500)
    db, url = provision.ensure(CREDS)
    assert db["id"] == "db1" and not create.called and url.endswith("@h/db?sslmode=require")
    assert listing.calls[0].request.url.params["project_id"] == "proj-1"


def test_db_user_override_skips_iam(monkeypatch):
    monkeypatch.setenv("SCW_DB_USER", "app-1")
    assert provision.principal_id(None, CREDS) == "app-1"


def test_missing_secrets(monkeypatch):
    for n in ("SCW_ACCESS_KEY", "SCW_SECRET_KEY", "SCW_DEFAULT_PROJECT_ID"):
        monkeypatch.delenv(n, raising=False)
    with pytest.raises(provision.ProvisionError, match="SCW_ACCESS_KEY"):
        provision.Credentials.from_env()


def test_migration_files_are_numbered():
    assert [v for v, _ in migrations()] == [1, 2]


def _local_test_db() -> bool:
    # the test drops the public schema: never run it against anything but a local server
    from urllib.parse import urlsplit

    url = os.environ.get("TEST_DATABASE_URL", "")
    return bool(url) and urlsplit(url).hostname in ("localhost", "127.0.0.1")


@pytest.mark.skipif(not _local_test_db(), reason="needs a local TEST_DATABASE_URL")
def test_migrate_on_real_postgres():
    import psycopg

    with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        assert migrate(conn) == [1, 2]
        assert migrate(conn) == []  # idempotent
        conn.execute("INSERT INTO feedback (artist_key, kind) VALUES ('asna', 'wrong')")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO feedback (artist_key, kind) VALUES ('x', 'love')")
