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

WEB_DIR = Path(__file__).parent / "web"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nightcrawler")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect venues and concerts, build the site")
    r.add_argument("--zone", default="config/zone.yaml")
    r.add_argument("--out", default="site")
    r.add_argument("--cache", default=".cache/http")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs may carry keys

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WEB_DIR, out, dirs_exist_ok=True)
    report = run(load_zone(args.zone), out, Fetcher(cache_dir=args.cache))

    summary = summary_markdown(report)
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
