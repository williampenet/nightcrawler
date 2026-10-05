"""Command line: `nightcrawler run --out site`."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
from pathlib import Path

from .config import load_zone
from .http import Fetcher
from .pipeline import run, summary_markdown
from .sources.osm import osmium_filter_expressions as osm_filters

WEB_DIR = Path(__file__).parent / "web"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nightcrawler")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect venues and concerts, build the site")
    r.add_argument("--zone", default="config/zone.yaml")
    r.add_argument("--out", default="site")
    r.add_argument("--cache", default=".cache/http")
    r.add_argument("--osm-extract", default=".cache/osm/venues.geojsonseq")
    o = sub.add_parser("osm-extract-plan", help="print shell variables for the CI OSM extract step")
    o.add_argument("--zone", default="config/zone.yaml")
    args = ap.parse_args(argv)

    if args.cmd == "osm-extract-plan":
        zone = load_zone(args.zone)
        print(f"OSM_URL={zone.osm_extract_url or ''}")
        print("OSM_BBOX=" + ",".join(str(x) for x in zone.bbox()))
        print("OSM_FILTERS='" + " ".join(osm_filters()) + "'")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs may carry keys

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WEB_DIR, out, dirs_exist_ok=True)
    try:
        report = run(
            load_zone(args.zone),
            out,
            Fetcher(cache_dir=args.cache),
            osm_extract=Path(args.osm_extract),
        )
    except Exception as exc:
        # annotations are readable where raw logs are not; messages never include secrets
        annotate("error", f"Pipeline failed: {type(exc).__name__}: {str(exc)[:500]}")
        raise

    annotate("notice", one_line(report))
    summary = summary_markdown(report)
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(summary)
    return 0


def annotate(level: str, message: str) -> None:
    """Emit a GitHub Actions annotation (no-op formatting outside Actions is harmless)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        safe = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{level}::{safe}", flush=True)


def one_line(report: dict) -> str:
    status = ", ".join(f"{k}={v}" for k, v in sorted(report["probe_status"].items()))
    method = ", ".join(f"{k}={v}" for k, v in sorted(report["probe_method"].items()))
    src = report.get("sources", {})
    tm = src.get("ticketmaster", {})
    return (
        f"osm={src.get('openstreetmap_venues')} ticketmaster={tm.get('status')} "
        f"(venues={tm.get('venues')} events={tm.get('events')}) "
        f"website_events={src.get('website_events')} | "
        f"venues={report['venues']} with_website={report['venues_with_website']} | "
        f"probe: {status} | methods: {method or '-'} | raw_events={report['raw_events']} "
        f"concerts={report['concerts']} venues_with_concerts={report['venues_with_concerts']}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
