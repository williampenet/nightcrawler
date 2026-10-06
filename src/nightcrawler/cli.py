"""Command line: `nightcrawler run --out site`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import yaml

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
    r.add_argument("--app-config", default="config/app.yaml")
    r.add_argument("--reference", default="eval/reference/watch_events.csv")
    r.add_argument(
        "--store", action="store_true", help="use the event store when SCW_* secrets are set"
    )
    sub.add_parser("store", help="create the Scaleway event store if needed and migrate it")
    sub.add_parser("deploy-feedback", help="package and deploy the feedback function (Scaleway)")
    o = sub.add_parser("osm-extract-plan", help="print shell variables for the CI OSM extract step")
    o.add_argument("--zone", default="config/zone.yaml")
    args = ap.parse_args(argv)

    if args.cmd == "store":
        return store_command()
    if args.cmd == "deploy-feedback":
        return deploy_feedback_command()

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
    write_app_config(Path(args.app_config), out / "app-config.json")
    try:
        report = run(
            load_zone(args.zone),
            out,
            # Deezer allows ~50 requests / 5 s; everyone else gets 1 request / s
            Fetcher(cache_dir=args.cache, host_intervals={"api.deezer.com": 0.2}),
            osm_extract=Path(args.osm_extract),
            database_url=store_url() if args.store else os.environ.get("DATABASE_URL") or None,
            reference=Path(args.reference),
        )
    except Exception as exc:
        # annotations are readable where raw logs are not; messages never include secrets
        annotate("error", f"Pipeline failed: {type(exc).__name__}: {str(exc)[:500]}")
        raise

    if report["store"]["status"].startswith("error"):
        annotate("warning", f"Event store: {report['store']['status']}; published without it")
    annotate("notice", one_line(report))
    summary = summary_markdown(report)
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(summary)
    return 0


STORE_SECRETS = ("SCW_ACCESS_KEY", "SCW_SECRET_KEY", "SCW_DEFAULT_PROJECT_ID")


def prepare_store() -> tuple[dict, str, list[int], int]:
    """Ensure the database exists and is migrated: (database, url, migrations, tables)."""
    import psycopg

    from .store.migrate import migrate
    from .store.provision import ensure

    db, url = ensure()
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{url}", flush=True)
    with psycopg.connect(url, connect_timeout=60, autocommit=True) as conn:
        applied = migrate(conn)
        tables = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchone()[0]
    return db, url, applied, tables


def store_url() -> str | None:
    """`run --store`: the database URL, kept inside this process (never exported), or None
    when the secrets are missing or the store fails; the run then goes on without it."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if not all(os.environ.get(n) for n in STORE_SECRETS):
        annotate("notice", "Event store: skipped: SCW_* secrets not set")
        return None
    try:
        return prepare_store()[1]
    except Exception as exc:  # libpq messages name the host and user: type only
        annotate("warning", f"Event store unavailable ({type(exc).__name__}); run without it")
        return None


def store_command() -> int:
    """CI only: ensure the database exists, migrate it, export DATABASE_URL to later steps."""
    import psycopg

    from .store.provision import ProvisionError

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not all(os.environ.get(n) for n in STORE_SECRETS):
        annotate("notice", "Event store: skipped: SCW_* secrets not set")
        return 0
    try:
        db, url, applied, tables = prepare_store()
    except (ProvisionError, psycopg.Error) as exc:
        # libpq and API messages may name the host or user: the type only, publicly
        annotate("error", f"Event store: {type(exc).__name__}")
        return 1
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if "\n" in url or "\r" in url:  # would inject extra variables into GITHUB_ENV
            annotate("error", "Event store: unexpected newline in the connection URL")
            return 1
        if path := os.environ.get("GITHUB_ENV"):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"DATABASE_URL={url}\n")
    annotate(
        "notice",
        f"Event store: {db.get('name')} {db.get('status')} in {db.get('region', 'fr-par')} "
        f"(cpu {db.get('cpu_min')}-{db.get('cpu_max')}), migrations applied: {applied or 'none'}, "
        f"tables: {tables}",
    )
    return 0


def deploy_feedback_command() -> int:
    """CI only: deploy functions/feedback with the database URL and the token hash as secrets."""
    import hashlib
    import tempfile

    import httpx

    from .store.deploy_function import build_zip, deploy, function_settings, smoke_test
    from .store.provision import Credentials, ProvisionError, ensure

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.environ.get("FEEDBACK_TOKEN", "")
    if not token:
        annotate("notice", "Feedback function: skipped: FEEDBACK_TOKEN not set")
        return 0
    origin = os.environ.get("ALLOWED_ORIGIN", "https://williampenet.github.io")
    try:
        creds = Credentials.from_env()
        _, url = ensure(creds)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::add-mask::{url}", flush=True)
        settings = function_settings(origin, url, hashlib.sha256(token.encode()).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            fn = deploy(creds, settings, build_zip(Path(tmp) / "feedback.zip"))
    except (ProvisionError, OSError, subprocess.CalledProcessError) as exc:
        annotate("error", f"Feedback function: {type(exc).__name__}: {str(exc)[:300]}")
        return 1
    except httpx.HTTPError as exc:  # its message may carry a signed upload URL: type only
        annotate("error", f"Feedback function: {type(exc).__name__}")
        return 1
    public = f"https://{fn.get('domain_name', '')}"
    preflight, refused, profile, root = smoke_test(public, origin)
    ok = (preflight, refused, profile, root) == (204, 401, 401, 405)
    annotate(
        "notice" if ok else "error",
        f"Feedback function: {fn.get('status')} at {public} (runtime {fn.get('runtime')}, "
        f"preflight HTTP {preflight}, wrong token HTTP {refused}, "
        f"GET /profile HTTP {profile}, GET / HTTP {root}); "
        "paste this URL into config/app.yaml feedback_url",
    )
    return 0 if ok else 1


PUBLIC_KEYS = {"spotify_client_id", "feedback_url"}  # only these settings reach the public page


def write_app_config(src: Path, dest: Path) -> None:
    data = yaml.safe_load(src.read_text(encoding="utf-8")) if src.exists() else {}
    if not isinstance(data, dict):
        data = {}
    public = {k: str(v) for k, v in data.items() if k in PUBLIC_KEYS and v}
    dest.write_text(json.dumps(public), encoding="utf-8")


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
    ga = src.get("gancio", {})
    art = report.get("artists", {})
    dd = report.get("dedup", {})
    st = report.get("store", {})
    store = st.get("status", "off")
    cov = report.get("coverage")  # numbers only: no event or venue name in the annotation
    if not cov:
        cover = "-"
    elif "status" in cov:
        cover = cov["status"]
    else:
        cover = (
            f"{cov['found']}/{cov['in_window']} rate={cov['rate']} "
            f"date_venue_only={cov['date_venue_only']}"
        )
    if store == "ok":
        store += "(" + " ".join(f"{k}={v}" for k, v in st.items() if k != "status") + ")"
    platforms = ",".join(
        f"{name}(" + " ".join(f"{k}={v}" for k, v in p.items()) + ")"
        for name, p in src.get("platforms", {}).items()
    )
    return (
        f"osm={src.get('openstreetmap_venues')} ticketmaster={tm.get('status')} "
        f"(venues={tm.get('venues')} events={tm.get('events')}) "
        f"gancio={ga.get('status')} (venues={ga.get('venues')} events={ga.get('events')}) "
        f"website_events={src.get('website_events')} platforms={platforms or '-'} | "
        f"venues={report['venues']} with_website={report['venues_with_website']} | "
        f"probe: {status} | methods: {method or '-'} | raw_events={report['raw_events']} "
        f"concerts={report['concerts']} venues_with_concerts={report['venues_with_concerts']} "
        f"dedup={dd.get('merged')}/{dd.get('conflicts')} store={store} | "
        f"artists: {art.get('identified')}/{art.get('candidates')} identified "
        f"({art.get('confident')} confident; doubts: ambiguous={art.get('doubt_ambiguous')} "
        f"unverified={art.get('doubt_unverified')} low_fans={art.get('doubt_low_fans')} "
        f"short={art.get('doubt_short_name')}), "
        f"{art.get('with_tags')} with tags, {art.get('concerts_with_artist')} concerts covered | "
        f"reference coverage: {cover}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
