"""End-to-end run: discover venues, probe them, collect concerts, write the site data."""

from __future__ import annotations

import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import coverage
from .artists import enrich
from .config import Zone
from .events import build_concerts
from .http import Fetcher
from .models import Probe, RawEvent, Venue
from .probe import PlatformBudget, probe_venue
from .sources import gancio, osm, page_llm, priority, ticketmaster
from .store import sync
from .venues import (
    attach_to_configured,
    configured_venue_ids,
    configured_venues,
    is_excluded,
    merge,
)

log = logging.getLogger(__name__)

WORKERS = 8


def run(
    zone: Zone,
    out_dir: Path,
    fetcher: Fetcher,
    now: datetime | None = None,
    osm_extract: Path | None = None,
    database_url: str | None = None,
    reference: Path | None = None,
    llm_ctx: page_llm.Context | None = None,
) -> dict:
    tz = ZoneInfo(zone.timezone)
    now = now or datetime.now(tz)

    # 1. venues: maps first, then ticketing (its events keep pointing to merged ids)
    osm_venues = osm.discover(zone, fetcher, extract=osm_extract)
    tm_venues, tm_events, tm_status = ticketmaster.collect(zone, fetcher, now, tz)
    try:
        ga_venues, ga_events, ga_status = gancio.collect(zone, fetcher, now, tz)
    except Exception as exc:  # optional source: never stop the run
        log.warning("Gancio failed: %s", type(exc).__name__)
        ga_venues, ga_events, ga_status = [], [], f"error: {type(exc).__name__}"
    # venues the user asked to drop (config/zone.yaml), filtered per source before the merge
    # so a merged name cannot hide them; their API and agenda events go with them
    dropped = {
        v.id for v in osm_venues + tm_venues + ga_venues if is_excluded(v, zone.excluded_venues)
    }
    osm_venues = [v for v in osm_venues if v.id not in dropped]
    tm_venues = [v for v in tm_venues if v.id not in dropped]
    ga_venues = [v for v in ga_venues if v.id not in dropped]
    tm_events = [ev for ev in tm_events if ev.venue_id not in dropped]
    ga_events = [ev for ev in ga_events if ev.venue_id not in dropped]
    venues, alias = merge([osm_venues, tm_venues, ga_venues])
    venues += configured_venues(zone.priority_venues, venues)  # "Mes salles" (WIP-64)
    configured_ids, configured_notes = configured_venue_ids(zone.priority_venues, venues)
    for ev in tm_events + ga_events:
        ev.venue_id = alias.get(ev.venue_id, ev.venue_id)
    by_id = {v.id: v for v in venues}

    # 2. probe venue websites in parallel (the fetcher rate-limits per host)
    probes: dict[str, Probe] = {}
    raw: list[RawEvent] = list(tm_events)
    budget = PlatformBudget()  # run-wide cap on ticketing platform pages

    def task(v: Venue) -> tuple[Probe, list[RawEvent]]:
        try:
            return probe_venue(v, fetcher, tz, budget)
        except Exception as exc:  # one broken site must not stop the run
            log.warning("probe failed for %s: %s", v.id, type(exc).__name__)
            return Probe(v.id, "fetch_error", detail=type(exc).__name__), []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for probe, events in pool.map(task, venues):
            probes[probe.venue_id] = probe
            raw.extend(events)
    # "Mes salles" readers (WIP-60, WIP-62, WIP-66): the venue's own data, attached by venue
    # name later; page_llm readers also need the extract_events task (llm_ctx)
    try:
        pv_events, pv_rows = priority.collect(
            zone.priority_venues, fetcher, now, tz, zone.window_days, llm_ctx
        )
    except Exception as exc:  # optional source: never stop the run
        log.warning("priority venue readers failed: %s", type(exc).__name__)
        pv_events = []
        pv_rows = [
            {"name": e["name"], "venue": e["venue"], "reader": e["reader"]["type"]}
            | {"status": f"error: {type(exc).__name__}", "events": 0, "pages": 0}
            for e in zone.priority_venues
        ]
    attach_to_configured(pv_events, configured_ids)  # to its venue_id or resolved name
    for row in pv_rows:
        if note := configured_notes.get(row["name"]):
            row["status"] = f"{row['status']}; {note}"
    raw.extend(pv_events)
    raw.extend(ga_events)  # after venue sites: on a duplicate, the venue's own page wins
    # venues covered by the ticketing API or an agenda count as readable even if their site is not
    for method, evs in (("ticketmaster", tm_events), ("gancio", ga_events)):
        for vid in {ev.venue_id for ev in evs} & probes.keys():
            if probes[vid].status != "structured":
                probes[vid].status, probes[vid].method = "structured", method

    # 3. concerts
    dedup: dict = {}
    concerts = build_concerts(
        raw,
        by_id,
        now=now,
        window_days=zone.window_days,
        tz=tz,
        stats=dedup,
        excluded=zone.excluded_venues,
    )
    # a venue read by its configured reader counts as readable (attached by name above)
    for c in concerts:
        p = probes.get(c.venue_id)
        readers = [s.split(":")[0] for s in c.sources if s.split(":")[0] in priority.READERS]
        if p and p.status != "structured" and readers:
            p.status, p.method = "structured", readers[0]
    store: dict = {"status": "off"}  # no DATABASE_URL: stateless run (ADR-0001)
    reported: set[str] = set()
    if database_url:  # stable ids, overrides, feedback (ADR-0005, WIP-46)
        concerts, store, reported = sync.sync(
            database_url, raw, concerts, by_id, now, zone.window_days, tz
        )
    per_venue = Counter(c.venue_id for c in concerts)
    cover = None  # FR-11 reference coverage (WIP-55), when the reference file is there
    if reference and reference.exists():
        try:
            matching = coverage.load_matching(reference.with_name("matching.yaml"))
            refs = coverage.load_reference(reference)
            cover = coverage.measure(refs, concerts, now, zone.window_days, tz, matching)
        except Exception as exc:  # a measure must never fail the run
            log.warning("reference coverage failed: %s", type(exc).__name__)
            cover = {"status": f"error: {type(exc).__name__}"}
    artists, artist_stats = enrich(concerts, fetcher)
    if store["status"] == "ok":
        store["reported_artists"] = sync.mark_reported(artists, reported)

    # 4. outputs
    report = {
        "generated_at": now.isoformat(),
        "zone": zone.__dict__,
        "venues": len(venues),
        "venues_excluded": len(dropped),
        "venues_with_website": sum(1 for v in venues if v.website),
        "probe_status": dict(Counter(p.status for p in probes.values())),
        "probe_method": dict(Counter(p.method for p in probes.values() if p.method)),
        "sources": {
            "openstreetmap_venues": len(osm_venues),
            "ticketmaster": {
                "status": tm_status,
                "venues": len(tm_venues),
                "events": len(tm_events),
            },
            "gancio": {
                "status": ga_status,
                "venues": len(ga_venues),
                "events": len(ga_events),
                # credited on the page (footer), built from config/zone.yaml
                "instances": [dict(i) for i in zone.gancio_instances],
            },
            # [{name, venue, reader, status, events, pages}] per configured venue
            "priority_venues": pv_rows,
            # pages sent to the extract_events model this run (cache hits excluded)
            "llm_pages": llm_ctx.budget.used if llm_ctx else 0,
            "website_events": len(raw) - len(tm_events) - len(ga_events) - len(pv_events),
            "platforms": platform_stats(probes.values()),
        },
        "raw_events": len(raw),
        "concerts": len(concerts),
        "concerts_ai_extracted": sum(c.ai_extracted for c in concerts),
        "dedup": dedup,  # {merged, conflicts, merge_examples, conflict_examples} (WIP-42)
        "venues_with_concerts": len(per_venue),
        "artists": artist_stats,
        # {status, raw_upserted, concerts_reused, concerts_new, overrides_applied,
        #  reported_artists} when the store answered
        "store": store,
        # {in_window, found, rate, per_venue: {venue: [in_window, found]}, events}
        "coverage": cover,
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
    _dump(data_dir / "artists.json", {k: a.to_dict() for k, a in sorted(artists.items())})
    _dump(data_dir / "report.json", report)
    return report


def platform_stats(probes) -> dict[str, dict[str, int]]:
    """Per platform: pages requested, pages with events, events (before attribution),
    pages blocked by robots.txt, pages skipped because the run-wide budget was spent."""
    stats: dict[str, dict[str, int]] = {}
    keys = ("pages", "with_events", "events", "robots_blocked", "budget_skipped")
    for probe in probes:
        for page in probe.platform_pages:
            s = stats.setdefault(page["platform"], dict.fromkeys(keys, 0))
            if page["status"] == "robots_blocked":
                s["robots_blocked"] += 1
            elif page["status"] == "skipped_budget":
                s["budget_skipped"] += 1
            else:
                s["pages"] += 1
                s["with_events"] += page["events"] > 0
                s["events"] += page["events"]
    return dict(sorted(stats.items()))


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
        f"| Duplicates merged / conflicts | {report['dedup']['merged']} "
        f"/ {report['dedup']['conflicts']} |",
        f"| Artists identified | {report['artists']['identified']} "
        f"/ {report['artists']['candidates']} |",
        f"| Concerts with an identified artist | {report['artists']['concerts_with_artist']} |",
    ]
    cov = report.get("coverage")  # totals only; per venue in data/report.json
    if cov and "status" in cov:
        lines.append(f"| Reference events found (FR-11) | {cov['status']} |")
    elif cov:
        rate = "n/a" if cov["rate"] is None else f"{cov['rate']:.0%}"
        lines += [
            f"| Reference events found (FR-11) | {cov['found']} / {cov['in_window']} ({rate}) |",
            f"| … same date and venue, no artist match | {cov['date_venue_only']} |",
        ]
    return "\n".join(lines) + "\n"
