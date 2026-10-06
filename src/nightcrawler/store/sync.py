"""Nightly sync with the event store (ADR-0005, WIP-46).

One short connection before artist enrichment: upsert the raw events, give each concert
of this run the id of the stored concert it matches (so ids stay stable across runs),
read the manual overrides and the "wrong match" feedback. Nothing is applied to the
in-memory result until the transaction has committed: on any error the pipeline goes on
with this run's own ids, exactly as without a store.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..dedup import concert_id, same_concert_across_runs
from ..models import Concert, RawEvent, Venue

log = logging.getLogger(__name__)

# the serverless database may be waking up from idle (ADR-0005)
CONNECT = {
    "connect_timeout": 60,
    # libpq keepalives: a dropped connection fails instead of hanging
    # (https://www.postgresql.org/docs/16/libpq-connect.html#LIBPQ-KEEPALIVES)
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}
STATEMENT_TIMEOUT = "60s"  # bounds every statement of the sync transaction
FIELDS = {f.name for f in dataclasses.fields(Concert)}


class EncodingError(RuntimeError):
    pass


def source_key(ev: RawEvent) -> str:
    # RawEvent carries no source id yet: URL, title and start identify a listing
    return f"{ev.url or ''}|{ev.title}|{ev.start.isoformat()}"


def _payload(ev: RawEvent) -> dict:
    return dataclasses.asdict(ev) | {"start": ev.start.isoformat()}


def _concert(data) -> Concert | None:
    """A stored concert, or None when its data is not a usable concert."""
    try:
        c = Concert(**{k: v for k, v in data.items() if k in FIELDS})
        datetime.fromisoformat(c.start)
        return c
    except (AttributeError, TypeError, ValueError):
        return None


def match(
    built: list[Concert], stored: list[tuple[str, list[str], dict]], venues: dict[str, Venue]
) -> dict[int, tuple[str, list[str], str]]:
    """{index in built: (stored id, stored aliases, rule)} for concerts already stored.

    Rule "id": this run's id is a stored id (first pass), else this run's id or one of
    its aliases is a stored id or alias. Rule "dedup": dedup.same_concert_across_runs()
    holds, and the pair is the only candidate on both sides among what is left.
    """
    out: dict[int, tuple[str, list[str], str]] = {}
    claimed: set[str] = set()
    tests = (
        lambda c, sid, sa: c.id == sid,
        lambda c, sid, sa: bool({c.id, *c.aliases} & {sid, *sa}),
    )
    for test in tests:
        for i, c in enumerate(built):
            if i in out:
                continue
            for sid, saliases, _ in stored:
                if sid not in claimed and test(c, sid, saliases):
                    out[i] = (sid, saliases, "id")
                    claimed.add(sid)
                    break
    left = [(sid, sa, _concert(d)) for sid, sa, d in stored if sid not in claimed]
    left = [s for s in left if s[2] is not None]
    todo = [i for i in range(len(built)) if i not in out]
    pairs = [(i, s) for i in todo for s in left if same_concert_across_runs(built[i], s[2], venues)]
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
        with connect(url, autocommit=True, **CONNECT) as conn:
            conn.execute("SET client_encoding TO 'UTF8'")
            if conn.info.encoding.lower().replace("-", "") != "utf8":
                raise EncodingError(conn.info.encoding)
            final, dropped_ids, reported = _write(
                conn, raw, concerts, venues, now, window_days, tz, report
            )
    except EncodingError:
        log.warning("event store: unexpected client encoding")
        return concerts, {"status": "error: encoding"}, set()
    except Exception as exc:  # the store must never fail the pipeline (ADR-0005)
        log.warning("event store unavailable: %s", type(exc).__name__)  # type only
        return concerts, {"status": f"error: {type(exc).__name__}"}, set()
    out = [c for c in final if not ({c.id, *c.aliases} & dropped_ids)]
    report["overrides_applied"] = len(final) - len(out)
    return out, {"status": "ok", **report}, reported


def _listing_ids(ev: RawEvent, c: Concert, tz: ZoneInfo) -> set[str]:
    """The ids dedupe() gives this raw event's own listing, at its page venue or the
    concert's (the venue may have been re-attributed)."""
    start = ev.start.astimezone(tz).isoformat()
    return {
        concert_id(Concert("", ev.title, start, vid, "", None, None, ev.performers, [], ""))
        for vid in {ev.venue_id, c.venue_id}
    }


def _members(c: Concert, raw: list[RawEvent], tz: ZoneInfo) -> list[int]:
    """Indexes of the raw events merged into c (c still holds this run's id and aliases)."""
    run_ids = {c.id, *c.aliases}
    urls = {link["url"] for link in c.links}
    out = []
    for n, ev in enumerate(raw):
        if ev.source not in c.sources:
            continue
        same = (
            ev.url in urls
            and ev.title == c.title
            and ev.start.astimezone(tz).isoformat() == c.start
        )
        if same or _listing_ids(ev, c, tz) & run_ids:
            out.append(n)
    return out


def _write(conn, raw, concerts, venues, now, window_days, tz, report):
    from psycopg.types.json import Jsonb

    first = now.date().isoformat()
    last = (now + timedelta(days=window_days)).date().isoformat()
    with conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
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
        report["raw_upserted"] = len(set(raw_ids))
        stored = conn.execute(
            "SELECT id, aliases, data FROM concerts "
            "WHERE left(data->>'start', 10) BETWEEN %s AND %s ORDER BY id",
            (first, last),
        ).fetchall()
        found = match(concerts, stored, venues)
        final: list[Concert] = []
        rows, links = [], []
        used: set[str] = set()
        for i, c in enumerate(concerts):
            sid, saliases, rule = found.get(i, (c.id, [], "new"))
            while sid in used:  # this run's id already taken by a reused concert
                sid, rule = hashlib.sha1(f"{sid}|{c.start}".encode()).hexdigest()[:12], "new"
            used.add(sid)
            aliases = sorted({c.id, *c.aliases, *saliases} - {sid})
            kept = dataclasses.replace(c, id=sid, aliases=aliases)
            final.append(kept)
            report["concerts_reused" if rule != "new" else "concerts_new"] += 1
            rows.append((sid, aliases, Jsonb(kept.to_dict())))
            links += [(sid, raw_ids[n], rule) for n in _members(c, raw, tz)]
        if rows:
            cur.executemany(
                "INSERT INTO concerts (id, aliases, data) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, last_seen = now(), "
                "aliases = ARRAY(SELECT DISTINCT unnest(concerts.aliases || EXCLUDED.aliases) "
                "ORDER BY 1)",
                rows,
            )
        if links:
            cur.executemany(
                "INSERT INTO concert_sources (concert_id, raw_id, rule) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                links,
            )
        # TODO(WIP-50): apply 'merge' and 'split' overrides; only 'not_concert' is applied
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
