"""Model eval for `judge_taste` (WIP-57, ADR-0006): `python -m eval.judge [--only id,id]`.

Judges William's labelled concerts and the FR-11 reference positives with every candidate of
`eval/models.yaml` (`judge_taste`), in two prompt conditions, and publishes aggregated metrics
only: one notice per candidate and condition, and a markdown table in the job summary. The repo
is public: never print a title, a name, an id, the profile, a prompt or a model's reason.

Data (read at run time, never committed): the profile and the rating history from the event
store (read-only, as the taste eval), the published site (concerts, artists, report coverage),
`eval/reference/watch_events.csv`. Method and decision rule: docs/adr/0006.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yaml

from eval.taste import load as taste
from nightcrawler import judge, llm
from nightcrawler.cli import annotate
from nightcrawler.coverage import load_reference

ROOT = Path(__file__).resolve().parent
LABELS_JS = ROOT / "labels.js"
REFERENCE = ROOT.parent / "reference" / "watch_events.csv"
CONDITIONS = ("profile", "profile+examples")
MAX_OUTPUT_TOKENS = 400
TIMEOUT_S = 120.0
RULE_BASELINE = "rule-based score (scoring.js, leave-one-out)"
log = logging.getLogger("eval.judge")


# ---------------------------------------------------------------- cases


def run_labels(payload: dict) -> list[dict]:
    """[{id, label, rule}] from eval/judge/labels.js (taste eval labels, same concerts)."""
    proc = subprocess.run(
        ["node", str(LABELS_JS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"labels runner exited with {proc.returncode}")
    return json.loads(proc.stdout)["labels"]


def row_concert(i: int, row: dict) -> dict:
    """A reference row as a concert: its own text (artists, venue, date), no artist data."""
    acts = [a.strip() for a in row["artists"].replace(" & ", " + ").split("+") if a.strip()]
    return {
        "id": f"ref{i}",
        "title": row["artists"],
        "lineup": acts,
        "venue_name": row["venue"],
        "start": row["date"],
        "artists": [],
    }


def build_cases(
    labels: list[dict], concerts: list[dict], reference: list[dict], coverage: dict | None
) -> list[dict]:
    """Cases {concert, kind, label, rule, watch}. kind: "rated" (William's label), "ref_matched"
    (reference row matched to a published concert, not rated), "ref_row" (judged from the
    row's text). A rated concert also reported by the watch keeps William's label (watch=True)."""
    by_id = {c["id"]: c for c in concerts}
    matched = {}  # concert id -> reference row index
    for e in (coverage or {}).get("events") or []:
        if e.get("found") and e.get("concert_id") in by_id:
            matched.setdefault(e["concert_id"], e["row"])
    cases = []
    for lab in labels:
        if (c := by_id.get(lab["id"])) is None:
            continue
        cases.append(
            {
                "concert": c,
                "kind": "rated",
                "label": lab["label"],
                "rule": lab["rule"],
                "watch": c["id"] in matched,
            }
        )
    rated = {c["concert"]["id"] for c in cases}
    rows_used = {row for cid, row in matched.items() if cid in rated}
    for cid, row in matched.items():
        if cid not in rated:
            cases.append({"concert": by_id[cid], "kind": "ref_matched", "label": "positive"})
            rows_used.add(row)
    for i, row in enumerate(reference):
        if i not in rows_used:
            cases.append({"concert": row_concert(i, row), "kind": "ref_row", "label": "positive"})
    return cases


# ---------------------------------------------------------------- calls


def spec_of(cand: dict) -> llm.ModelSpec:
    return llm.ModelSpec.from_dict(cand)


def judge_one(spec: llm.ModelSpec, messages: list[dict], client: httpx.Client) -> dict:
    """{data, latency, tin, tout, error}: data None when unusable (error = a code, never text)."""
    try:
        a = llm.chat_json(
            spec,
            messages,
            judge.SCHEMA,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.0,
            timeout_s=TIMEOUT_S,
            client=client,
        )
    except llm.ModelError:
        return {"data": None, "latency": None, "tin": 0, "tout": 0, "error": "transport"}
    data = a.data
    if data is not None and judge.check(data):
        data, a.reason = None, "check"
    return {
        "data": data,
        "latency": a.latency_s,
        "tin": a.tokens_in,
        "tout": a.tokens_out,
        "error": a.reason,
    }


def run_condition(
    cand: dict, cond: str, cases: list[dict], artists: dict, profile: dict, workers: int
) -> list[dict]:
    spec = spec_of(cand)
    rated = [(c["concert"], c["label"]) for c in cases if c["kind"] == "rated"]

    def one(case: dict) -> dict:
        ex = judge.pick_examples(case["concert"], rated) if cond == "profile+examples" else ()
        return judge_one(spec, judge.messages_for(case["concert"], artists, profile, ex), client)

    with httpx.Client() as client, ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, cases))


# ---------------------------------------------------------------- metrics


def wilson(k: int, n: int, z: float = 1.959963984540054) -> list[float] | None:
    if not n:
        return None
    p, d = k / n, 1 + z * z / n
    mid, half = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(mid - half, 3), round(mid + half, 3)]


def pairwise(liked: list[float], disliked: list[float]) -> float | None:
    """P(score(liked) > score(disliked)), ties 0.5: the taste eval's definition."""
    pairs = len(liked) * len(disliked)
    if not pairs:
        return None
    wins = sum(1 if a > b else 0.5 if a == b else 0 for a in liked for b in disliked)
    return round(wins / pairs, 3)


def ratio(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def metrics(cand: dict, cases: list[dict], answers: list[dict]) -> dict:
    verdict = [a["data"]["verdict"] if a["data"] else None for a in answers]
    # an unusable answer ranks last and counts as "no" (conservative); validity is reported
    sc = [judge.score(a["data"]) if a["data"] else -1.0 for a in answers]
    pos = [i for i, c in enumerate(cases) if c["label"] in ("liked", "positive")]
    picked = [i for i in pos if verdict[i] in judge.PICKED]
    with_disc = [i for i in pos if verdict[i] in (*judge.PICKED, "discovery")]
    rated = [i for i, c in enumerate(cases) if c["kind"] == "rated"]
    rated_picked = [i for i in rated if verdict[i] in judge.PICKED]
    rated_liked_picked = [i for i in rated_picked if cases[i]["label"] == "liked"]
    sub = {
        k: [i for i in pos if cases[i]["kind"] == k] for k in ("rated", "ref_matched", "ref_row")
    }
    lat = sorted(a["latency"] for a in answers if a["latency"] is not None)
    tin = [a["tin"] for a in answers if a["latency"] is not None]
    tout = [a["tout"] for a in answers if a["latency"] is not None]
    price = cand.get("price_eur_per_mtok") or {}
    cost = None
    if tin and price:
        cost = round(
            (statistics.mean(tin) * price["in"] + statistics.mean(tout) * price["out"]) / 1000, 3
        )  # € per 1,000 judgements
    errors: dict[str, int] = {}
    for a in answers:
        if a["data"] is None:
            errors[a["error"] or "unknown"] = errors.get(a["error"] or "unknown", 0) + 1
    return {
        "n": len(cases),
        "valid": ratio(sum(a["data"] is not None for a in answers), len(answers)),
        "errors": errors,
        "recall": ratio(len(picked), len(pos)),
        "recall_wilson95": wilson(len(picked), len(pos)),
        "recall_with_discovery": ratio(len(with_disc), len(pos)),
        "recall_by_kind": {
            k: [sum(verdict[i] in judge.PICKED for i in v), len(v)] for k, v in sub.items()
        },
        "precision": ratio(len(rated_liked_picked), len(rated_picked)),
        "precision_wilson95": wilson(len(rated_liked_picked), len(rated_picked)),
        "picked_rated": len(rated_picked),
        "pairwise": pairwise(
            [sc[i] for i in rated if cases[i]["label"] == "liked"],
            [sc[i] for i in rated if cases[i]["label"] == "disliked"],
        ),
        "verdicts": {v: verdict.count(v) for v in (*judge.VERDICTS, None) if verdict.count(v)},
        "p50_s": round(statistics.median(lat), 2) if lat else None,
        "p95_s": round(lat[min(len(lat) - 1, math.ceil(0.95 * len(lat)) - 1)], 2) if lat else None,
        "tokens_in": round(statistics.mean(tin)) if tin else None,
        "tokens_out": round(statistics.mean(tout)) if tout else None,
        "eur_per_1000": cost,
    }


def references(cases: list[dict]) -> dict:
    """Baselines on the same labels: the rule-based score and the former watch (ADR-0006)."""
    rated = [c for c in cases if c["kind"] == "rated"]
    watch = [c for c in rated if c["watch"]]
    pos = [c for c in cases if c["label"] in ("liked", "positive")]
    return {
        "labels": len(rated),
        "liked": sum(c["label"] == "liked" for c in rated),
        "disliked": sum(c["label"] == "disliked" for c in rated),
        "positives": len(pos),
        "positives_by_kind": {
            k: sum(c["kind"] == k for c in pos) for k in ("rated", "ref_matched", "ref_row")
        },
        "rule_pairwise": pairwise(
            [c["rule"] for c in rated if c["label"] == "liked"],
            [c["rule"] for c in rated if c["label"] == "disliked"],
        ),
        "watch_rated": len(watch),
        "watch_liked": sum(c["label"] == "liked" for c in watch),
        "watch_precision": ratio(sum(c["label"] == "liked" for c in watch), len(watch)),
    }


# ---------------------------------------------------------------- output


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def ci(w) -> str:
    return "" if not w else f" ({w[0]:.0%}–{w[1]:.0%})"


def compact(cid: str, cond: str, m: dict) -> str:
    return (
        f"judge_taste {cid} [{cond}]: recall {pct(m['recall'])}, precision "
        f"{pct(m['precision'])} on {m['picked_rated']} picked, pairwise {pct(m['pairwise'])}, "
        f"valid {pct(m['valid'])}, p95 {m['p95_s']} s, €{m['eur_per_1000']} / 1,000"
    )


def table(ref: dict, rows: list[tuple[str, str, dict]], skipped: dict[str, str]) -> str:
    out = [
        "### Model eval — task `judge_taste`",
        "",
        f"William's labels: {ref['labels']} ({ref['liked']} liked, {ref['disliked']} disliked). "
        f"Positives: {ref['positives']} (rated {ref['positives_by_kind']['rated']}, reference "
        f"matched {ref['positives_by_kind']['ref_matched']}, reference row text "
        f"{ref['positives_by_kind']['ref_row']}).",
        "",
        f"References on the same labels: {RULE_BASELINE} pairwise {pct(ref['rule_pairwise'])}; "
        f"former watch (Claude): {ref['watch_liked']}/{ref['watch_rated']} of its rated picks "
        f"liked ({pct(ref['watch_precision'])}).",
        "",
        "| Candidate | Condition | Recall picked (95 % CI) | + discovery | Precision on labels "
        "(95 % CI) | Pairwise | Valid | p95 s | Tokens in / out | € / 1,000 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cid, cond, m in rows:
        out.append(
            f"| {cid} | {cond} | {pct(m['recall'])}{ci(m['recall_wilson95'])} | "
            f"{pct(m['recall_with_discovery'])} | {pct(m['precision'])}"
            f"{ci(m['precision_wilson95'])} on {m['picked_rated']} | {pct(m['pairwise'])} | "
            f"{pct(m['valid'])} | {m['p95_s']} | {m['tokens_in']} / {m['tokens_out']} | "
            f"{m['eur_per_1000']} |"
        )
    for cid, why in skipped.items():
        out.append(f"| {cid} | — | skipped: {why} | | | | | | | |")
    out += [
        "",
        "Recall by subset (picked / n): "
        + "; ".join(
            f"{cid} [{cond}] "
            + ", ".join(f"{k} {a}/{b}" for k, (a, b) in m["recall_by_kind"].items())
            for cid, cond, m in rows
        ),
        "",
    ]
    return "\n".join(out)


def candidates(only: str) -> list[dict]:
    cands = yaml.safe_load((ROOT.parent / "models.yaml").read_text("utf-8"))["judge_taste"]
    cands = [c for c in cands if not c.get("retired") and c["provider"] not in ("gold", "none")]
    if only:
        wanted = {i.strip() for i in only.split(",") if i.strip()}
        cands = [c for c in cands if c["id"] in wanted]
    return cands


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="python -m eval.judge")
    ap.add_argument("--site", default=os.environ.get("TASTE_SITE", "site"))
    ap.add_argument("--only", default=os.environ.get("JUDGE_ONLY", ""))
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-calls", type=int, default=2500, help="cost guard for the whole run")
    ap.add_argument("--results", default=str(ROOT / "results" / "latest.json"))
    args = ap.parse_args(argv)
    conds = [c for c in args.conditions.split(",") if c in CONDITIONS]

    try:
        url = taste.database_url()
    except Exception as exc:  # API messages may name the account: the type only
        annotate("error", f"Judge eval: event store lookup failed ({type(exc).__name__})")
        return 1
    if not url:
        annotate("notice", "Judge eval: skipped: no DATABASE_URL or SCW_* secrets")
        return 0
    taste.mask(url)
    try:
        store = taste.load_store(url)
        site = taste.load_site(args.site)
        labels = run_labels({**store, "concerts": site["concerts"], "artists": site["artists"]})
    except Exception as exc:  # libpq and HTTP messages may name hosts: the type only
        annotate("error", f"Judge eval: data unavailable ({type(exc).__name__})")
        return 1
    profile = store["state"] or {}
    cases = build_cases(labels, site["concerts"], load_reference(REFERENCE), site["coverage"])
    ref = references(cases)
    taste_len = len(profile.get("taste_text") or "") if isinstance(profile, dict) else 0
    annotate(
        "notice",
        f"Judge eval: {len(cases)} cases, {ref['labels']} labels, "
        f"{ref['positives']} positives; written taste {taste_len} chars; site data "
        f"{site['generated_at']}",
    )

    rows, skipped, results, calls = [], {}, {}, 0
    for cand in candidates(args.only):
        ok, why = spec_of(cand).available()
        if not ok:
            skipped[cand["id"]] = why
            continue
        for cond in conds:
            if calls + len(cases) > args.max_calls:
                skipped[f"{cand['id']} [{cond}]"] = f"cost guard ({args.max_calls} calls)"
                continue
            calls += len(cases)
            log.info("candidate %s, condition %s", cand["id"], cond)
            answers = run_condition(cand, cond, cases, site["artists"], profile, args.workers)
            m = metrics(cand, cases, answers)
            annotate("notice", compact(cand["id"], cond, m))
            rows.append((cand["id"], cond, m))
            results[f"{cand['id']} [{cond}]"] = m
    md = table(ref, rows, skipped)
    print(md)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(md)
    out = Path(args.results)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"references": ref, "results": results, "skipped": skipped}, indent=1), "utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
