"""Apply the SQL files of store/sql in order, once each (ADR-0005)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

SQL_DIR = Path(__file__).parent / "sql"
NAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def migrations(sql_dir: Path = SQL_DIR) -> list[tuple[int, Path]]:
    found = []
    for path in sql_dir.iterdir():
        m = NAME_RE.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    found.sort()
    versions = [v for v, _ in found]
    if len(set(versions)) != len(versions):
        raise ValueError("two migrations share a version number")
    return found


def migrate(conn, sql_dir: Path = SQL_DIR) -> list[int]:
    """Apply pending migrations, each in its own transaction; returns the versions applied."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for version, path in migrations(sql_dir):
        if version in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
        log.info("applied migration %03d", version)
        applied.append(version)
    return applied
