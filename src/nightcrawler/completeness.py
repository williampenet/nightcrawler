"""How far ahead each source reads, and which caps were hit (WIP-107): dates and counts only.

The collection horizon went from 60 to up to 400 days (config/zone.yaml `window_days`). This
measure shows whether the sources actually deliver beyond the former 60 days, and whether a cap
rather than the horizon decided what was kept:

- per source family (content.family of each concert's sources): published concerts, those
  starting more than BEYOND_DAYS days after today, and the farthest concert date;
- cap hits by kind, read from the status notes the readers already write (`detail_cap`,
  `page_cap`, `chunk_cap`, `llm_cap`, `detail_run_cap`; Gancio and Ticketmaster append theirs
  to their status, Gancio also `geocode_cap`) plus the artist lookup cap (artists.MAX_LOOKUPS,
  `lookup_cap`);
- per "Mes salles" venue (its configured name, public in config/zone.yaml): the farthest event
  date its reader read (`last` of the priority row) and its cap notes.

No event title, artist or text leaves this module: the report and the annotation are public.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from zoneinfo import ZoneInfo

from .content import family
from .models import Concert

BEYOND_DAYS = 60  # the former horizon: what the 400-day horizon adds is counted beyond it
CAP_KINDS = ("detail_cap", "detail_run_cap", "page_cap", "chunk_cap", "llm_cap", "geocode_cap")


def cap_notes(status: str | None) -> list[str]:
    """The cap kinds named in a status, one per note ("ok; page_cap: url; detail_cap: 40" ->
    ["page_cap", "detail_cap"]). A note may carry a prefix ("Ville Morte: detail_cap: ...")."""
    found = []
    for note in (status or "").split("; "):
        for kind in CAP_KINDS:
            # whole token: "detail_cap" must not match inside "detail_run_cap"
            if note.startswith(kind + ":") or note == kind or f": {kind}:" in note:
                found.append(kind)
                break
    return found


def measure(
    concerts: Iterable[Concert],
    priority_rows: list[dict],
    statuses: dict[str, str],
    artist_stats: dict | None,
    now: datetime,
    tz: ZoneInfo,
) -> dict:
    """{beyond_days, by_source: {family: {concerts, beyond, last}}, caps: {kind: {source: hits}},
    venues: [{name, last, caps}]}. `statuses`: other sources' statuses ({"gancio": ...})."""
    today = now.astimezone(tz).date()
    by_source: dict[str, dict] = {}
    for c in concerts:
        day = datetime.fromisoformat(c.start).astimezone(tz).date()
        beyond = (day - today).days > BEYOND_DAYS
        for fam in sorted({family(s) for s in c.sources}):
            row = by_source.setdefault(fam, {"concerts": 0, "beyond": 0, "last": None})
            row["concerts"] += 1
            row["beyond"] += beyond
            row["last"] = max(row["last"] or "", day.isoformat())
    caps: dict[str, dict[str, int]] = {}

    def hit(kind: str, source: str) -> None:
        caps.setdefault(kind, {})
        caps[kind][source] = caps[kind].get(source, 0) + 1

    venues = []
    for row in priority_rows:
        kinds = cap_notes(row.get("status"))
        for kind in kinds:
            hit(kind, row.get("reader") or "?")
        venues.append({"name": row.get("name"), "last": row.get("last"), "caps": kinds})
    for source, status in sorted(statuses.items()):
        for kind in cap_notes(status):
            hit(kind, source)
    if (artist_stats or {}).get("capped"):
        hit("lookup_cap", "artists")
    return {
        "beyond_days": BEYOND_DAYS,
        "by_source": dict(sorted(by_source.items())),
        "caps": {k: dict(sorted(v.items())) for k, v in sorted(caps.items())},
        "venues": venues,
    }


def _caps_text(caps: dict) -> str:
    if not caps:
        return "none"
    return " ".join(
        f"{kind}={sum(by.values())}(" + ",".join(f"{s}*{n}" for s, n in by.items()) + ")"
        for kind, by in caps.items()
    )


def text(m: dict | None) -> str:
    """One line for the run annotation: per source concerts beyond the former horizon /
    concerts and the farthest date, cap hits by kind, then each "Mes salles" venue's farthest
    date read and cap notes. Dates and counts only, plus the configured venue names."""
    if not m:
        return "-"
    if "status" in m:
        return str(m["status"])
    sources = " ".join(
        f"{fam}={r['beyond']}/{r['concerts']}@{r['last'] or '-'}"
        for fam, r in m["by_source"].items()
    )
    venues = ", ".join(
        f"{v['name']}@{v['last'] or '-'}" + (f"[{'+'.join(v['caps'])}]" if v["caps"] else "")
        for v in m["venues"]
    )
    return (
        f"beyond {m['beyond_days']}d/concerts@last: {sources or '-'} | "
        f"caps: {_caps_text(m['caps'])} | Mes salles last read: {venues or '-'}"
    )


def summary_rows(m: dict | None) -> list[str]:
    """Markdown table rows for the run summary."""
    if not m or "status" in m:
        return [f"| Horizon completeness (WIP-107) | {text(m)} |"]
    rows = [f"| Concerts beyond {m['beyond_days']} days / concerts, farthest date | |"]
    for fam, r in m["by_source"].items():
        rows.append(f"| … {fam} | {r['beyond']} / {r['concerts']}, {r['last'] or '-'} |")
    rows.append(f"| Cap hits | {_caps_text(m['caps'])} |")
    for v in m["venues"]:
        note = f" ({', '.join(v['caps'])})" if v["caps"] else ""
        rows.append(f"| … {v['name']}: farthest date read | {v['last'] or '-'}{note} |")
    return rows
