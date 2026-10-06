"""Taste eval loader (WIP-52, docs/TASTE_EVAL.md).

Reads the profile and the rating history from the event store (read-only transaction,
statement timeout), the published site data (concerts.json, artists.json), runs
eval/taste/run.js on them and publishes aggregated counts and rates only: one `::notice`
annotation and the job summary. The repo is public: never print a name, title, id, the
profile or the connection details.

    python -m eval.taste.load --site https://<owner>.github.io/<repo>/   # or a local site/ dir

Database: DATABASE_URL, else looked up (never created) with the SCW_* secrets, like
`run --store`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from nightcrawler.cli import STORE_SECRETS, annotate
from nightcrawler.store.sync import CONNECT

RUNNER = Path(__file__).with_name("run.js")
STATEMENT_TIMEOUT = "30s"
SITE_FILES = ("concerts", "artists", "report")
TIERS = (
    ("sure", "sure (score ≥ 0.9)"),
    ("inferred", "inferred (0 < score < 0.9)"),
    ("none", "none (score 0)"),
)


def database_url() -> str | None:
    if url := os.environ.get("DATABASE_URL"):
        return url
    if not all(os.environ.get(n) for n in STORE_SECRETS):
        return None
    from nightcrawler.store.provision import ensure

    return ensure(create=False)[1]


def read_store(conn) -> dict:
    """{state, feedback}: the profile data (None without one) and the rating events that
    name a concert, oldest first (rows of one request share created_at: id keeps order)."""
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        row = conn.execute("SELECT data FROM profile WHERE id = 'me'").fetchone()
        events = conn.execute(
            "SELECT concert_id, kind FROM feedback "
            "WHERE concert_id IS NOT NULL AND kind IN ('like', 'unlike', 'dislike') "
            "ORDER BY created_at, id"
        ).fetchall()
        # dates of the disliked concerts, to tell past ones from ids lost since (WIP-55)
        ids = sorted({c for c, k in events if k == "dislike"})
        stored = conn.execute(
            "SELECT id, aliases, left(data->>'start', 10) FROM concerts "
            "WHERE id = ANY(%s) OR aliases && %s::text[]",
            (ids, ids),
        ).fetchall()
    found = {}  # id or alias -> the stored concert's id and date (a stored id wins)
    for cid, aliases, day in stored:
        for i in aliases or []:
            found.setdefault(i, {"id": cid, "date": day})
    for cid, _, day in stored:
        found[cid] = {"id": cid, "date": day}
    return {
        "state": row[0] if row else None,
        "feedback": [{"concert_id": c, "kind": k} for c, k in events],
        "stored_concerts": found,
    }


def load_store(url: str, connect=None) -> dict:
    if connect is None:
        import psycopg

        connect = psycopg.connect
    with connect(url, autocommit=True, **CONNECT) as conn:
        return read_store(conn)


def load_site(source: str, get=None) -> dict:
    """{concerts, artists, generated_at} from a site directory or the published site URL."""
    data = {}
    if source.startswith(("https://", "http://")):
        if get is None:
            import httpx

            get = httpx.get
        base = source.rstrip("/") + "/data/"
        nonce = str(int(time.time()))  # asks the Pages CDN for a fresh copy (unverified)
        for name in SITE_FILES:
            r = get(f"{base}{name}.json", params={"v": nonce}, timeout=60, follow_redirects=True)
            r.raise_for_status()
            data[name] = r.json()
    else:
        for name in SITE_FILES:
            path = Path(source) / "data" / f"{name}.json"
            data[name] = json.loads(path.read_text(encoding="utf-8"))
    return {
        "concerts": data["concerts"],
        "artists": data["artists"],
        "generated_at": (data["report"] or {}).get("generated_at"),
    }


def run_eval(payload: dict) -> dict:
    """Aggregated result of run.js (it reads the payload on stdin; nothing is echoed)."""
    proc = subprocess.run(
        ["node", str(RUNNER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"taste runner exited with {proc.returncode}")
    return json.loads(proc.stdout)


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _ci(t: dict) -> str:
    return "n/a" if not t["wilson95"] else f"{t['wilson95'][0]:.0%}–{t['wilson95'][1]:.0%}"


def dislike_text(d: dict | None) -> str:
    """Where the "Pas pour moi" ratings went (WIP-55): counts only."""
    if not d:
        return ""
    return (
        f'"Pas pour moi": {d["concerts"]} concerts in the rating history ({d["rows"]} rows), '
        f"{d['labelled']} labelled; not labelled: {d['rated_again']} rated again, "
        f"{d['not_hidden']} no longer hidden in the profile, {d['ambiguous']} ambiguous, "
        f"{d['unpublished_past']} past, {d['unpublished_upcoming']} upcoming but not published, "
        f"{d['unpublished_unknown']} unknown to the store. Hidden in the profile: "
        f"{d['hidden_in_profile']} ({d['hidden_published']} published)."
    )


def one_line(result: dict) -> str:
    lab, pw = result["labels"], result["pairwise"]
    if not lab["total"]:
        return f"Taste eval: no labels yet ({result['concerts']} concerts published)"
    tiers = ", ".join(
        f"{k} {t['liked']}/{t['n']} liked (precision {_pct(t['precision'])})"
        for k, t in ((k, result["tiers"][k]) for k, _ in TIERS)
    )
    return (
        f"Taste eval: {lab['total']} labels ({lab['liked']} liked, {lab['disliked']} disliked); "
        f"{tiers}; pairwise accuracy {_pct(pw['accuracy'])} over {pw['pairs']} pairs"
    )


def summary_markdown(result: dict, generated_at: str | None) -> str:
    lab, pw = result["labels"], result["pairwise"]
    lines = [
        "## Taste eval (WIP-52)",
        "",
        f"Site data generated at {generated_at or 'unknown'}; {result['concerts']} concerts. "
        "Method and how to read: docs/TASTE_EVAL.md.",
        "",
        f"Labels: {lab['total']} ({lab['liked']} liked, {lab['disliked']} disliked; "
        f"{lab['from_feedback']} from the rating history, {lab['from_state_only']} from the "
        f"profile only). Rating events on concerts no longer published: "
        f"{lab['feedback_events_on_unpublished_concerts']}. Not labelled: "
        f"{lab['stale_feedback']} stale ratings (undone since), "
        f"{lab['ambiguous_dislikes']} ambiguous dislikes.",
        "",
    ]
    if dis := dislike_text(result.get("dislikes")):
        lines += [dis, ""]
    if not lab["total"]:
        return "\n".join([*lines, "No labels yet: nothing to measure.", ""])
    lines += [
        "| Tier | n | liked | disliked | precision | Wilson 95 % |",
        "|---|---|---|---|---|---|",
    ]
    for key, title in TIERS:
        t = result["tiers"][key]
        lines.append(
            f"| {title} | {t['n']} | {t['liked']} | {t['disliked']} | "
            f"{_pct(t['precision'])} | {_ci(t)} |"
        )
    lines += [
        "",
        f"Pairwise ranking accuracy (liked above disliked, ties = 0.5): "
        f"{_pct(pw['accuracy'])} over {pw['pairs']} pairs.",
        "",
    ]
    return "\n".join(lines)


def publish(result: dict, generated_at: str | None) -> None:
    annotate("notice", one_line(result))
    summary = summary_markdown(result, generated_at)
    print(summary)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(summary)


def mask(url: str) -> None:
    """Masks the URL and, separately, its host, user and password in the Actions log."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    parts = urlsplit(url)
    values = [url, parts.hostname, parts.username, parts.password]
    values += [unquote(v) for v in (parts.username, parts.password) if v]
    for value in dict.fromkeys(v for v in values if v and "\n" not in v and "\r" not in v):
        print(f"::add-mask::{value}", flush=True)


def main(argv: list[str] | None = None) -> int:
    # httpx logs every request URL at INFO; keep the log to warnings
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", default=os.environ.get("TASTE_SITE", "site"))
    args = ap.parse_args(argv)
    try:
        url = database_url()
    except Exception as exc:  # API messages may name the account: the type only
        annotate("error", f"Taste eval: event store lookup failed ({type(exc).__name__})")
        return 1
    if not url:
        annotate("notice", "Taste eval: skipped: no DATABASE_URL or SCW_* secrets")
        return 0
    mask(url)
    try:
        store = load_store(url)
    except Exception as exc:  # libpq messages name the host and user: the type only
        annotate("error", f"Taste eval: event store unavailable ({type(exc).__name__})")
        return 1
    try:
        site = load_site(args.site)
    except Exception as exc:
        annotate("error", f"Taste eval: site data unavailable ({type(exc).__name__})")
        return 1
    try:
        today = (site["generated_at"] or "")[:10] or None  # the site data's local date
        payload = {**store, "concerts": site["concerts"], "artists": site["artists"]}
        result = run_eval(payload | {"today": today})
    except Exception as exc:
        annotate("error", f"Taste eval: runner failed ({type(exc).__name__})")
        return 1
    publish(result, site["generated_at"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
