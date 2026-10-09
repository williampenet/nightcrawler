"""Taste judgements in the event store (ADR-0007, WIP-83).

The pipeline reads what a judgement needs (written profile, ratings, listing descriptions, the
input hash of the stored judgements) in one read-only transaction, closes it during the model
calls, then saves the new judgements in a short write transaction. Verdicts and reasons are
personal data: they stay in the store and are served to the page by the feedback function with
the send key; nothing here prints or logs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATEMENT_TIMEOUT = "30s"
# verdicts of concerts that started longer ago are deleted on save: GET /verdicts serves from
# yesterday on, so a week only leaves room for late or re-dated listings (data minimisation)
RETENTION_DAYS = 7
RATING_KINDS = ("like", "unlike", "dislike")


@dataclass
class Inputs:
    profile: dict = field(default_factory=dict)  # profile.data of 'me' ({} without one)
    # id the page sent (current id or alias) -> (liked | disliked | None for an unlike, time)
    ratings: dict[str, tuple[str | None, Any]] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)  # concert id -> listing text
    known: dict[str, str] = field(default_factory=dict)  # concert id -> stored input_hash


def read_descriptions(conn, ids: list[str]) -> dict[str, str]:
    """{concert id: the longest description among its stored listings}; ties: the first stored
    listing (ORDER BY), so the choice is the same every run. Venue text (raw_events.payload),
    linked to its concert by concert_sources."""
    rows = conn.execute(
        "SELECT cs.concert_id, r.payload->>'description' FROM concert_sources cs "
        "JOIN raw_events r ON r.id = cs.raw_id "
        "WHERE cs.concert_id = ANY(%s) AND coalesce(r.payload->>'description', '') <> '' "
        "ORDER BY cs.concert_id, r.id",
        (ids,),
    ).fetchall()
    out: dict[str, str] = {}
    for cid, text in rows:
        if len(text) > len(out.get(cid, "")):
            out[cid] = text
    return out


def read_ratings(conn) -> dict[str, tuple[str | None, Any]]:
    """The latest like / unlike / dislike per rated concert id, with its time (a click stores one
    row per artist key with the same kind, so any of them is the click): like -> "liked",
    dislike -> "disliked", unlike -> None (kept, so that an unlike under one id can undo a like
    under an alias). Ids are the ones the page sent (current ids or aliases): the caller maps
    them to published concerts and keeps the latest per concert (WIP-84)."""
    rows = conn.execute(
        "SELECT DISTINCT ON (concert_id) concert_id, kind, created_at FROM feedback "
        "WHERE concert_id IS NOT NULL AND kind = ANY(%s) "
        "ORDER BY concert_id, created_at DESC, id DESC",
        (list(RATING_KINDS),),
    ).fetchall()
    label = {"like": "liked", "dislike": "disliked", "unlike": None}
    return {cid: (label[k], at) for cid, k, at in rows}


def load_inputs(conn, ids: list[str]) -> Inputs:
    """Everything a judging run reads, in one read-only transaction with a statement timeout."""
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        row = conn.execute("SELECT data FROM profile WHERE id = 'me'").fetchone()
        known = conn.execute(
            "SELECT concert_id, input_hash FROM verdicts WHERE concert_id = ANY(%s)", (ids,)
        ).fetchall()
        return Inputs(
            profile=row[0] if row and isinstance(row[0], dict) else {},
            ratings=read_ratings(conn),
            descriptions=read_descriptions(conn, ids),
            known=dict(known),
        )


def save(conn, rows: list[dict[str, Any]]) -> int:
    """Upserts one judgement per concert ({concert_id, input_hash, verdict, confidence, reason,
    section, model, starts_at}) and deletes those of concerts started more than RETENTION_DAYS
    ago; one transaction. Every concert_id must be stored in `concerts` (foreign key): one
    unknown id rolls the whole batch back, so the caller saves only concerts the sync stored.
    Returns the number of rows written."""
    with conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        if rows:
            conn.cursor().executemany(
                "INSERT INTO verdicts (concert_id, input_hash, verdict, confidence, reason, "
                "section, model, starts_at) VALUES (%(concert_id)s, %(input_hash)s, "
                "%(verdict)s, %(confidence)s, %(reason)s, %(section)s, %(model)s, %(starts_at)s) "
                "ON CONFLICT (concert_id) DO UPDATE SET input_hash = EXCLUDED.input_hash, "
                "verdict = EXCLUDED.verdict, confidence = EXCLUDED.confidence, "
                "reason = EXCLUDED.reason, section = EXCLUDED.section, model = EXCLUDED.model, "
                "starts_at = EXCLUDED.starts_at, judged_at = now()",
                rows,
            )
        conn.execute(
            "DELETE FROM verdicts WHERE starts_at < now() - make_interval(days => %s)",
            (RETENTION_DAYS,),
        )
    return len(rows)
