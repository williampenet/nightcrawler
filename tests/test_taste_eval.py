"""Taste eval loader (WIP-52): store read, site data, publication of aggregates only."""

import json
import os
import shutil
from urllib.parse import urlsplit

import pytest

from eval.taste import load

LIKED, DISLIKED, UNRATED = "aaaaaaaaaaa1", "ddddddddddd1", "bbbbbbbbbbb1"
CONCERTS = [
    {"id": LIKED, "title": "Earth live", "start": "2026-10-10T20:00:00+02:00",
     "performers": ["Earth"], "artists": ["earth"], "aliases": ["aaaaaaaaaaa0"]},
    {"id": DISLIKED, "title": "Popstar tour", "start": "2026-10-11T20:00:00+02:00",
     "performers": ["Popstar"], "artists": ["popstar"], "aliases": []},
    {"id": UNRATED, "title": "Boris night", "start": "2026-10-12T20:00:00+02:00",
     "performers": ["Boris"], "artists": ["boris"], "aliases": []},
]  # fmt: skip
ARTISTS = {
    "earth": {"key": "earth", "name": "Earth", "related": [], "tags": ["drone"]},
    "popstar": {"key": "popstar", "name": "Popstar", "related": [], "tags": ["pop"]},
    "boris": {"key": "boris", "name": "Boris", "related": ["Earth"], "tags": ["drone"]},
}
PROFILE = {
    "seeds": [{"name": "Earth", "tags": ["drone"]}],
    "liked": ["earth"],
    "disliked": ["popstar"],
    "hidden": [DISLIKED],
}
PRIVATE = [LIKED, DISLIKED, UNRATED, "aaaaaaaaaaa0", "Earth", "earth", "Popstar", "Boris"]
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _local_test_db() -> bool:
    # the test drops the public schema: never run it against anything but a local server
    url = os.environ.get("TEST_DATABASE_URL", "")
    return bool(url) and urlsplit(url).hostname in ("localhost", "127.0.0.1")


def _site(tmp_path, concerts=CONCERTS):
    data = tmp_path / "site" / "data"
    data.mkdir(parents=True)
    report = {"generated_at": "2026-10-06T06:17:00+02:00"}
    for name, value in (("concerts", concerts), ("artists", ARTISTS), ("report", report)):
        (data / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    return str(tmp_path / "site")


class RecordingConn:
    """Answers read_store's queries and records every statement."""

    def __init__(self, profile, events, concerts=()):
        self.profile, self.events, self.concerts, self.sql = profile, events, list(concerts), []
        self.params = []

    def transaction(self):
        import contextlib

        return contextlib.nullcontext()

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.params.append(params)
        rows = {
            "profile": [(self.profile,)] if self.profile else [],
            "feedback": self.events,
            "concerts": self.concerts,
        }
        table = next((t for t in rows if f"FROM {t}" in sql), None)
        return type("Cur", (), {"fetchone": lambda s: (rows[table] or [None])[0],
                                "fetchall": lambda s: rows[table]})()  # fmt: skip


def test_read_store_is_read_only_with_a_timeout():
    conn = RecordingConn(PROFILE, [(LIKED, "like")])
    got = load.read_store(conn)
    assert conn.sql[0] == "SET TRANSACTION READ ONLY"
    assert conn.sql[1].startswith("SET LOCAL statement_timeout")
    assert got == {
        "state": PROFILE,
        "feedback": [{"concert_id": LIKED, "kind": "like"}],
        "stored_concerts": {},
    }
    assert load.read_store(RecordingConn(None, []))["state"] is None


def test_read_store_gives_the_dates_of_disliked_concerts():
    conn = RecordingConn(
        PROFILE,
        [(DISLIKED, "dislike"), (DISLIKED, "dislike"), (LIKED, "like")],
        [(DISLIKED, ["ddddddddddd0"], "2026-10-01")],
    )
    got = load.read_store(conn)
    assert conn.params[-1] == ([DISLIKED], [DISLIKED])  # disliked ids only, once
    stored = {"id": DISLIKED, "date": "2026-10-01"}
    assert got["stored_concerts"] == {DISLIKED: stored, "ddddddddddd0": stored}


def test_summary_reports_where_the_dislikes_went():
    d = dict.fromkeys(
        ("labelled", "rated_again", "not_hidden", "ambiguous", "unpublished_past",
         "unpublished_upcoming", "unpublished_unknown", "hidden_published"),
        1,
    ) | {"rows": 9, "concerts": 7, "hidden_in_profile": 3}  # fmt: skip
    text = load.dislike_text(d)
    assert text.startswith('"Pas pour moi": 7 concerts in the rating history (9 rows), 1 labelled')
    assert "1 upcoming but not published, 1 unknown to the store" in text
    assert text.endswith("Hidden in the profile: 3 (1 published).")
    assert load.dislike_text(None) == ""


def test_load_site_from_a_directory_and_a_url(tmp_path):
    site = load.load_site(_site(tmp_path))
    assert site["concerts"] == CONCERTS and site["generated_at"].startswith("2026-10-06")
    asked = []

    class Resp:
        def __init__(self, path):
            self.path = path

        def raise_for_status(self):
            pass

        def json(self):
            return json.loads((tmp_path / "site" / "data" / self.path).read_text())

    def get(url, **kw):
        asked.append(url)
        return Resp(url.rsplit("/", 1)[1])

    site2 = load.load_site("https://example.org/nc", get=get)
    assert site2 == site
    assert asked[0] == "https://example.org/nc/data/concerts.json"


def test_summary_without_labels():
    result = {
        "concerts": 3,
        "labels": dict.fromkeys(
            (
                "total",
                "liked",
                "disliked",
                "from_feedback",
                "from_state_only",
                "unliked_by_feedback",
                "stale_feedback",
                "ambiguous_dislikes",
                "feedback_events_on_unpublished_concerts",
            ),
            0,
        ),  # fmt: skip
        "tiers": {
            k: {"n": 0, "liked": 0, "disliked": 0, "precision": None, "wilson95": None}
            for k in ("sure", "inferred", "none")
        },  # fmt: skip
        "pairwise": {"pairs": 0, "accuracy": None},
    }
    assert load.one_line(result) == "Taste eval: no labels yet (3 concerts published)"
    assert "No labels yet" in load.summary_markdown(result, None)


def test_skipped_without_credentials(monkeypatch, capsys):
    for n in ("DATABASE_URL", "SCW_ACCESS_KEY", "SCW_SECRET_KEY", "SCW_DEFAULT_PROJECT_ID"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert load.main(["--site", "nowhere"]) == 0
    assert "::notice::Taste eval: skipped" in capsys.readouterr().out


SECRET_URL = "postgresql://u-secret:p%40ss@db.secret-host:5432/x?sslmode=require"
LEAK = 'connection to "db.secret-host" user "u-secret" failed: Earth aaaaaaaaaaa1'


def _raise(exc):
    def f(*args, **kwargs):
        raise exc

    return f


@pytest.mark.parametrize(
    "step, patch, message",
    [
        ("lookup", "database_url", "event store lookup failed (ProvisionError)"),
        ("store", "load_store", "event store unavailable (OperationalError)"),
        ("site", "load_site", "site data unavailable (HTTPStatusError)"),
        ("runner", "run_eval", "runner failed (RuntimeError)"),
    ],
)
def test_errors_print_the_exception_type_only(step, patch, message, tmp_path, monkeypatch, capsys):
    import httpx
    import psycopg

    from nightcrawler.store.provision import ProvisionError

    errors = {
        "lookup": ProvisionError(f"list databases: HTTP 403 {LEAK}"),
        "store": psycopg.OperationalError(LEAK),
        "site": httpx.HTTPStatusError(
            LEAK, request=httpx.Request("GET", "https://x"), response=None
        ),
        "runner": RuntimeError(LEAK),
    }
    monkeypatch.setenv("DATABASE_URL", SECRET_URL)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(load, "load_store", lambda url: {"state": None, "feedback": []})
    monkeypatch.setattr(load, patch, _raise(errors[step]))
    assert load.main(["--site", _site(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert f"::error::Taste eval: {message}" in out
    public = "\n".join(line for line in out.splitlines() if not line.startswith("::add-mask::"))
    for secret in ("secret-host", "u-secret", "p@ss", "Earth", "aaaaaaaaaaa1"):
        assert secret not in public, secret


def test_host_user_and_password_are_masked_separately(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    load.mask(SECRET_URL)
    masks = {line[len("::add-mask::") :] for line in capsys.readouterr().out.splitlines()}
    assert {SECRET_URL, "db.secret-host", "u-secret", "p%40ss", "p@ss"} <= masks


@needs_node
def test_end_to_end_with_a_fake_store_publishes_aggregates_only(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/unused")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    store = {
        "state": PROFILE,
        "feedback": [
            {"concert_id": "aaaaaaaaaaa0", "kind": "like"},  # an alias of LIKED
            {"concert_id": DISLIKED, "kind": "dislike"},
        ],
    }
    monkeypatch.setattr(load, "load_store", lambda url: store)
    assert load.main(["--site", _site(tmp_path)]) == 0
    out = capsys.readouterr().out
    notices = [line for line in out.splitlines() if line.startswith("::notice::")]
    # "%" is escaped as "%25" in workflow commands (cli.annotate)
    assert notices == [
        "::notice::Taste eval: 2 labels (1 liked, 1 disliked); sure 1/1 liked (precision 100%25), "
        "inferred 0/0 liked (precision n/a), none 0/1 liked (precision 0%25); "
        "pairwise accuracy 100%25 over 1 pairs"
    ]
    text = out + summary.read_text(encoding="utf-8")
    assert '"Pas pour moi": 1 concerts in the rating history (1 rows), 1 labelled' in text
    for secret in PRIVATE:
        assert secret not in text, secret


@needs_node
@pytest.mark.skipif(not _local_test_db(), reason="needs a local TEST_DATABASE_URL")
def test_on_real_postgres(tmp_path, monkeypatch, capsys):
    import psycopg
    from psycopg.types.json import Jsonb

    from nightcrawler.store.migrate import migrate

    url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        migrate(conn)
        conn.execute("INSERT INTO profile (data) VALUES (%s)", (Jsonb(PROFILE),))
        # one request: same created_at, insertion order decides (like, then unlike)
        conn.execute(
            "INSERT INTO feedback (concert_id, artist_key, kind, created_at) VALUES "
            "(%s, 'boris', 'like', '2026-10-01'), (%s, 'boris', 'unlike', '2026-10-01'), "
            "(%s, 'earth', 'like', '2026-10-02'), (%s, 'popstar', 'dislike', '2026-10-03'), "
            "(NULL, 'earth', 'like', '2026-10-04'), (%s, 'earth', 'wrong', '2026-10-05')",
            (UNRATED, UNRATED, LIKED, DISLIKED, LIKED),
        )
    store = load.load_store(url)
    assert store["state"] == PROFILE
    assert [e["kind"] for e in store["feedback"]] == ["like", "unlike", "like", "dislike"]

    class Writer:
        """A real connection that tries a write inside read_store's transaction."""

        def __init__(self, conn):
            self.conn = conn

        def transaction(self):
            return self.conn.transaction()

        def execute(self, sql, params=None):
            cur = self.conn.execute(sql, params)
            if "FROM profile" in sql:
                self.conn.execute("DELETE FROM feedback")
            return cur

    with psycopg.connect(url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            load.read_store(Writer(conn))
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert load.main(["--site", _site(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Taste eval: 2 labels (1 liked, 1 disliked)" in out
    for secret in PRIVATE:
        assert secret not in out, secret
