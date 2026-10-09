"""What the taste judge can read about each concert (WIP-89): counts per source family.

A judgement is only as good as the concert's text: with a title alone, the model guesses (the
reason of "La Nuit du Gouyad 2" named an artist from the written taste, unrelated to the
evening). This measure says, per source, how many published concerts carry a line-up, an
identified artist and a listing description, and how many carry nothing but their title.
Counts only: the report is public, and listing texts stay in the store.
"""

from __future__ import annotations

from collections.abc import Iterable

from .artists import norm
from .models import Concert

LONG_DESCRIPTION = 300  # characters: a presentation, not a one-line teaser
KEYS = ("concerts", "lineup", "identified", "description", "long_description", "title_only")


def family(source: str) -> str:
    """Source family: "gancio:host" -> "gancio", "platform:shotgun" -> "platform:shotgun"
    (each platform is its own family: that is what the measure compares)."""
    head, _, rest = source.partition(":")
    return source if head == "platform" else head


def has_lineup(concert: Concert) -> bool:
    """A billed act other than the title itself (a listing often repeats its title as the
    only performer: "LA NUIT DU GOUYAD 2")."""
    title = norm(concert.title or "")
    acts = concert.lineup or concert.performers or []
    return any((k := norm(a)) and k != title for a in acts)


def measure(
    concerts: Iterable[Concert], artists: dict, descriptions: dict[str, str] | None
) -> dict:
    """{"all": counts, "by_source": {family: counts}, "descriptions": "store" | "off"}.
    `descriptions` maps a concert id to its longest stored listing text (store.verdicts
    .read_descriptions); None when the store was not read, then the description counts are
    left out rather than reported as zero."""
    desc = descriptions or {}
    out = {"all": dict.fromkeys(KEYS, 0), "by_source": {}}
    for c in concerts:
        text = desc.get(c.id) or ""
        row = {
            "concerts": 1,
            "lineup": has_lineup(c),
            "identified": any(getattr(artists.get(k), "confident", False) for k in c.artists),
            "description": bool(text.strip()),
            "long_description": len(text.strip()) >= LONG_DESCRIPTION,
        }
        row["title_only"] = not (row["lineup"] or row["identified"] or row["description"])
        targets = [out["all"]]
        for fam in sorted({family(s) for s in c.sources}):
            targets.append(out["by_source"].setdefault(fam, dict.fromkeys(KEYS, 0)))
        for t in targets:
            for k in KEYS:
                t[k] += int(row[k])
    out["by_source"] = dict(sorted(out["by_source"].items()))
    if descriptions is None:
        for t in [out["all"], *out["by_source"].values()]:
            for k in ("description", "long_description", "title_only"):
                t.pop(k)
    out["descriptions"] = "off" if descriptions is None else "store"
    return out


def read_descriptions(database_url: str, ids: list[str]) -> dict[str, str]:
    """The longest stored listing text per concert, in a read-only transaction with the store's
    statement timeout (the same read as the judging step)."""
    import psycopg

    from .store import verdicts
    from .store.sync import CONNECT

    with psycopg.connect(database_url, autocommit=True, **CONNECT) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET LOCAL statement_timeout = '{verdicts.STATEMENT_TIMEOUT}'")
            return verdicts.read_descriptions(conn, ids)


def text(m: dict | None) -> str:
    """One line for the run annotation: counts only."""
    if not m:
        return "-"
    if "status" in m:
        return str(m["status"])

    def part(name: str, t: dict) -> str:
        return f"{name}(" + " ".join(f"{k}={t[k]}" for k in KEYS if k in t) + ")"

    rows = [part("all", m["all"])] + [part(k, v) for k, v in m["by_source"].items()]
    return f"descriptions={m['descriptions']} " + " ".join(rows)
