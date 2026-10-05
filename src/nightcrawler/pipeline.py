"""End-to-end run: discover venues, probe them, collect concerts, write the site data."""

from __future__ import annotations

import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Zone
from .events import build_concerts
from .http import Fetcher
from .models import Probe, RawEvent, Venue
from .probe import probe_venue
from .sources import osm, ticketmaster
from .venues import merge

log = logging.getLogger(__name__)

WORKERS = 8


def run(zone: Zone, out_dir: Path, fetcher: Fetcher, now: datetime | None = None) -> dict:
    tz = ZoneInfo(zone.timezone)
    now = now or datetime.now(tz)

    # 1. venues: maps first, then ticketing (its events keep pointing to merged ids)
    osm_venues = osm.discover(zone, fetcher)
    tm_venues, tm_events = ticketmaster.collect(zone, fetcher, now, tz)
    venues, alias = merge([osm_venues, tm_venues])
    for ev in tm_events:
        ev.venue_id = alias.get(ev.venue_id, ev.venue_id)
    by_id = {v.id: v for v in venues}

    # 2. probe venue websites in parallel (the fetcher rate-limits per host)
    probes: dict[str, Probe] = {}
    raw: list[RawEvent] = list(tm_events)

    def task(v: Venue) -> tuple[Probe, list[RawEvent]]:
        try:
            return probe_venue(v, fetcher, tz)
        except Exception as exc:  # one broken site must not stop the run
            log.warning("probe failed for %s: %s", v.id, type(exc).__name__)
            return Probe(v.id, "fetch_error", detail=type(exc).__name__), []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for probe, events in pool.map(task, venues):
            probes[probe.venue_id] = probe
            raw.extend(events)
    # venues covered by the ticketing API count as readable even if their site is not
    for vid in {ev.venue_id for ev in tm_events}:
        if probes[vid].status != "structured":
            probes[vid].status, probes[vid].method = "structured", "ticketmaster"

    # 3. concerts
    concerts = build_concerts(raw, by_id, now=now, window_days=zone.window_days, tz=tz)
    per_venue = Counter(c.venue_id for c in concerts)

    # 4. outputs
    report = {
        "generated_at": now.isoformat(),
        "zone": zone.__dict__,
        "venues": len(venues),
        "venues_with_website": sum(1 for v in venues if v.website),
        "probe_status": dict(Counter(p.status for p in probes.values())),
        "probe_method": dict(Counter(p.method for p in probes.values() if p.method)),
        "raw_events": len(raw),
        "concerts": len(concerts),
        "venues_with_concerts": len(per_venue),
    }
    venue_rows = []
    for v in sorted(venues, key=lambda v: v.name.lower()):
        p = probes.get(v.id) or Probe(v.id, "no_website")
        row = v.to_dict() | {"probe": p.to_dict(), "concerts": per_venue.get(v.id, 0)}
        venue_rows.append(row)

    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    _dump(data_dir / "concerts.json", [c.to_dict() for c in concerts])
    _dump(data_dir / "venues.json", venue_rows)
    _dump(data_dir / "report.json", report)
    return report


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def summary_markdown(report: dict) -> str:
    lines = [
        f"## Nightcrawler run — {report['zone']['name']}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Venues found | {report['venues']} |",
        f"| … with a website | {report['venues_with_website']} |",
    ]
    for status, n in sorted(report["probe_status"].items()):
        lines.append(f"| Probe: {status} | {n} |")
    for method, n in sorted(report["probe_method"].items()):
        lines.append(f"| Method: {method} | {n} |")
    lines += [
        f"| Raw events | {report['raw_events']} |",
        f"| Concerts (next {report['zone']['window_days']} days) | {report['concerts']} |",
        f"| Venues with concerts | {report['venues_with_concerts']} |",
    ]
    return "\n".join(lines) + "\n"
