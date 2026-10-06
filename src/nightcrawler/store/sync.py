"""Nightly sync with the event store (ADR-0005, WIP-46).

One short connection before artist enrichment: upsert the raw events, give each concert
of this run the id of the stored concert it matches (so ids stay stable across runs),
read the manual overrides and the "wrong match" feedback. Nothing is applied to the
in-memory result until the transaction has committed: on any error the pipeline goes on
with this run's own ids, exactly as without a store.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..dedup import same_concert
from ..models import Concert, RawEvent, Venue

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 60  # the serverless database may be waking up from idle (ADR-0005)
FIELDS = {f.name for f in dataclasses.fields(Concert)}


def source_key(ev: RawEvent) -> str:
    # RawEvent carries no source id yet: its URL (else title) and start identify it
    return f"{ev.url or ev.title}|{ev.start.isoformat()}"


def _payload(ev: RawEvent) -> dict:
    return dataclasses.asdict(ev) | {"start": ev.start.isoformat()}


def match(
    built: list[Concert], stored: list[tuple[str, list[str], dict]], venues: dict[str, Venue]
) -> dict[int, tuple[str, list[str], str]]:
    """{index in built: (stored id, stored aliases, rule)} for concerts already stored.

    Rule "id": this run's id or one of its aliases is a stored id or alias.
    Rule "dedup": dedup.same_concert() holds, and the pair is the only candidate on both
    sides among the concerts left after the "id" pass.
    """
    out: dict[int, tuple[str, list[str], str]] = {}
    claimed: set[str] = set()
    for i, c in enumerate(built):
        ids = {c.id, *c.aliases}
        for sid, saliases, _ in stored:
            if sid not in claimed and ids & {sid, *saliases}:
                out[i] = (sid, saliases, "id")
                claimed.add(sid)
                break
    left = [
        (sid, sa, Concert(**{k: v for k, v in d.items() if k in FIELDS})) for sid, sa, d in stored
    ]
    left = [s for s in left if s[0] not in claimed]
    todo = [i for i in range(len(built)) if i not in out]
    pairs = [(i, s) for i in todo for s in left if same_concert(built[i], s[2], venues)]
    for i, s in pairs:
        if sum(p[0] == i for p in pairs) == 1 and sum(p[1] is s for p in pairs) == 1:
            out[i] = (s[0], s[1], "dedup")
    return out


def sync(
    url: str,
    raw: list[RawEvent],
    concerts: list[Concert],
    venues: dict[str, Venue],
    now: datetime,
    window_days: int,
    tz: ZoneInfo,
    connect=None,
) -> tuple[list[Concert], dict, set[str]]:
    """(concerts to publish, report part, artist keys reported as wrong matches)."""
    if connect is None:
        import psycopg

        connect = psycopg.connect
    report = dict.fromkeys(
        ("raw_upserted", "concerts_reused", "concerts_new", "overrides_applied"), 0
    )
    try:
        with connect(url, connect_timeout=CONNECT_TIMEOUT_S, autocommit=True) as conn:
            final, dropped_ids, reported = _write(
                conn, raw, concerts, venues, now, window_days, tz, report
            )
    except Exception as exc:  # the store must never fail the pipeline (ADR-0005)
        log.warning("event store unavailable: %s", type(exc).__name__)
        return concerts, {"status": f"error: {type(exc).__name__}"}, set()
    out = [c for c in final if not ({c.id, *c.aliases} & dropped_ids)]
    report["overrides_applied"] = len(final) - len(out)
    return out, {"status": "ok", **report}, reported


def _write(conn, raw, concerts, venues, now, window_days, tz, report):
    from psycopg.types.json import Jsonb

    first = now.date().isoformat()
    last = (now + timedelta(days=window_days)).date().isoformat()
    with conn.transaction():
        cur = conn.cursor()
        raw_ids: list[int] = []
        if raw:
            cur.executemany(
                "INSERT INTO raw_events (source, source_key, payload) VALUES (%s, %s, %s) "
                "ON CONFLICT (source, source_key) DO UPDATE "
                "SET payload = EXCLUDED.payload, last_seen = now() RETURNING id",
                [(ev.source, source_key(ev), Jsonb(_payload(ev))) for ev in raw],
                returning=True,
            )
            while True:
                raw_ids.append(cur.fetchone()[0])
                if not cur.nextset():
                    break
        report["raw_upserted"] = len(raw_ids)
        stored = conn.execute(
            "SELECT id, aliases, data FROM concerts "
            "WHERE left(data->>'start', 10) BETWEEN %s AND %s ORDER BY id",
            (first, last),
        ).fetchall()
        found = match(concerts, stored, venues)
        final: list[Concert] = []
        used: set[str] = set()
        for i, c in enumerate(concerts):
            sid, saliases, rule = found.get(i, (c.id, [], "new"))
            if sid in used:  # this run's id already taken by a reused concert
                sid, rule = f"{c.id}-{i}", "new"
            used.add(sid)
            aliases = sorted({c.id, *c.aliases, *saliases} - {sid})
            kept = dataclasses.replace(c, id=sid, aliases=aliases)
            final.append(kept)
            report["concerts_reused" if rule != "new" else "concerts_new"] += 1
            conn.execute(
                "INSERT INTO concerts (id, aliases, data) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, last_seen = now(), "
                "aliases = ARRAY(SELECT DISTINCT unnest(concerts.aliases || EXCLUDED.aliases) "
                "ORDER BY 1)",
                (sid, aliases, Jsonb(kept.to_dict())),
            )
            links = {link["url"] for link in c.links}
            for ev, rid in zip(raw, raw_ids, strict=True):
                if (
                    ev.source in c.sources
                    and ev.start.astimezone(tz).date().isoformat() == c.start[:10]
                    and (ev.url in links or ev.ticket_url in links or ev.title == c.title)
                ):
                    conn.execute(
                        "INSERT INTO concert_sources (concert_id, raw_id, rule) "
                        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        (sid, rid, rule),
                    )
        # TODO(WIP-46 follow-up, no ticket yet): apply 'merge' and 'split' overrides;
        # only 'not_concert' is applied for now
        dropped = {
            str(r[0])
            for r in conn.execute(
                "SELECT payload->>'concert_id' FROM overrides WHERE kind = 'not_concert'"
            )
            if r[0]
        }
        reported = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT artist_key FROM feedback "
                "WHERE kind = 'wrong' AND artist_key IS NOT NULL"
            )
        }
    return final, dropped, reported


def mark_reported(artists: dict, keys: set[str]) -> int:
    """Artists reported as wrong matches lose their related artists and tags, like a
    doubtful identity (WIP-40), so no device guesses from them. Returns how many."""
    hit = keys & artists.keys()
    for k in hit:
        a = artists[k]
        a.related, a.tags, a.confident, a.doubt = [], [], False, "reported"
    return len(hit)
