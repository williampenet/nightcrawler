"""What the taste judge can read about each concert (WIP-89): counts per source family.

A judgement is only as good as the concert's text: with a title alone, the model guesses (the
reason of "La Nuit du Gouyad 2" named an artist from the written taste, unrelated to the
evening). This measure says, per source, how many published concerts carry a line-up, an
identified artist and a listing description, as the judge's prompt shows them (judge.acts,
judge.artist_lines, judge.description_text), and how many carry nothing but their title.
Counts only: the report is public, and listing texts stay in the store.
"""

from __future__ import annotations

from collections.abc import Iterable

from . import judge
from .artists import norm
from .models import Concert

LONG_DESCRIPTION = 300  # characters the judge reads: a presentation, not a one-line teaser
KEYS = ("concerts", "lineup", "identified", "description", "long_description", "title_only")
SHORT = {"long_description": "desc300", "description": "desc"}  # annotation column names


def family(source: str) -> str:
    """Source family: "gancio:host" -> "gancio", "platform:shotgun" -> "platform:shotgun"
    (each platform is its own family: that is what the measure compares)."""
    head, _, _ = source.partition(":")
    return source if head == "platform" else head


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold() or (bool(norm(a)) and norm(a) == norm(b))


def has_lineup(concert: Concert) -> bool:
    """An act in the judge's "À l'affiche" line other than the title itself (a listing often
    repeats its title as the only performer: "LA NUIT DU GOUYAD 2")."""
    title = judge._clean(concert.title)
    return any(not _same(a, title) for a in judge.acts(concert.to_dict()))


def measure(
    concerts: Iterable[Concert],
    artists: dict,
    descriptions: dict[str, str] | None,
    descriptions_status: str | None = None,
) -> dict:
    """{"all": counts, "by_source": {family: counts}, "descriptions": status}.
    `descriptions` maps a concert id to its longest stored listing text (read_descriptions);
    None when the store was not read (status "off", or `descriptions_status` such as
    "error: OperationalError"), then the description counts are left out rather than reported
    as zero."""
    desc = descriptions or {}
    out = {"all": dict.fromkeys(KEYS, 0), "by_source": {}}
    for c in concerts:
        text = judge.description_text(desc.get(c.id))
        keys = c.artists[: judge.MAX_ACTS]  # the ones judge.artist_lines describes
        row = {
            "concerts": 1,
            "lineup": has_lineup(c),
            "identified": any(getattr(artists.get(k), "confident", False) for k in keys),
            "description": bool(text),
            "long_description": len(text) >= LONG_DESCRIPTION,
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
    out["descriptions"] = "store" if descriptions is not None else descriptions_status or "off"
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
    """One line for the run annotation, column names once: counts only."""
    if not m:
        return "-"
    if "status" in m:
        return str(m["status"])
    cols = [k for k in KEYS if k in m["all"]]
    rows = [("all", m["all"]), *m["by_source"].items()]
    values = " ".join(f"{name}=" + "/".join(str(t[k]) for k in cols) for name, t in rows)
    head = "/".join(SHORT.get(k, k) for k in cols)
    return f"descriptions={m['descriptions']} cols={head} {values}"


def summary_rows(m: dict | None) -> list[str]:
    """Markdown table rows for the run summary (counts only)."""
    if not m or "status" in m:
        return [f"| What the judge reads (WIP-89) | {text(m)} |"]
    cols = [k for k in KEYS if k in m["all"]]
    head = " / ".join(SHORT.get(k, k) for k in cols)
    rows = [f"| What the judge reads: {head} (descriptions: {m['descriptions']}) | |"]
    for name, t in [("all", m["all"]), *m["by_source"].items()]:
        rows.append(f"| … {name} | " + " / ".join(str(t[k]) for k in cols) + " |")
    return rows
