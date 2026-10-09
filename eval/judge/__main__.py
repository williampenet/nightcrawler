"""Model eval for `judge_taste` (WIP-57, ADR-0006): `python -m eval.judge [--only id,id]`.

Judges William's labelled concerts and the FR-11 reference positives with every candidate of
`eval/judge/models.yaml`, in the prompt conditions of CONDITIONS, and publishes aggregated metrics
only: one notice per candidate and condition, and a markdown table in the job summary. The repo
is public: never print a title, a name, an id, the profile, a prompt or a model's reason.

Data (read at run time, never committed): the profile and the rating history from the event
store (read-only, as the taste eval), the published site (concerts, artists, report coverage),
`eval/reference/watch_events.csv`. Method and decision rule: docs/adr/0006.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import statistics
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

from eval.taste import load as taste
from nightcrawler import judge, llm
from nightcrawler.cli import annotate
from nightcrawler.coverage import load_reference
from nightcrawler.store import verdicts

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT.parent.parent / "config" / "models.yaml"  # routing (tasks.judge_taste)
LABELS_JS = ROOT / "labels.js"
REFERENCE = ROOT.parent / "reference" / "watch_events.csv"
# condition -> (example selection, share of the rated pool kept, subsample seed); None = none
# (WIP-81: "nn" = nearest ratings; "@1/3#1" = a third of the ratings, seed 1 = learning curve)
CONDITIONS: dict[str, tuple[str, float, int] | None] = {
    "profile": None,
    "profile+examples": ("random", 1.0, 0),
    "nn": ("nearest", 1.0, 0),
    "nn@2/3#1": ("nearest", 2 / 3, 1),
    "nn@2/3#2": ("nearest", 2 / 3, 2),
    "nn@1/3#1": ("nearest", 1 / 3, 1),
    "nn@1/3#2": ("nearest", 1 / 3, 2),
}
DEFAULT_CONDITIONS = ("profile+examples", "nn", "nn@2/3#1", "nn@2/3#2", "nn@1/3#1", "nn@1/3#2")
TARGET_RECALLS = (0.80, 0.90)  # cross-validated operating points (ADR-0006 iteration 2)
MAX_OUTPUT_TOKENS = 400
TIMEOUT_S = 120.0
# a candidate × condition stops calling after this many failed calls in a row (unknown model id,
# unsupported parameter, outage): the rest of its cases are "aborted", not paid for
STOP_AFTER = 10
FAILED_CALL = frozenset({"transport", "timeout", "aborted"})
# every prompt holds personal data: only EU providers (Scaleway Paris) or a local model
EU_PROVIDERS = frozenset({"scaleway", "local"})
TZ = ZoneInfo("Europe/Paris")  # reference rows are local dates (eval/reference/README.md)
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


def load_descriptions(url: str, ids: list[str], connect=None) -> dict[str, str]:
    """{concert id: the longest description among its stored listings} (WIP-79), read like the
    production judge reads it (store.verdicts.read_descriptions, ADR-0007), in a read-only
    transaction with a statement timeout. Venue text: sent to the EU model only, never printed."""
    if connect is None:
        import psycopg

        connect = psycopg.connect
    with connect(url, autocommit=True, **taste.CONNECT) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(f"SET LOCAL statement_timeout = '{taste.STATEMENT_TIMEOUT}'")
        return verdicts.read_descriptions(conn, ids)


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
    matched = {}  # concert id -> reference row index (the first one)
    rows_used = set()  # every reference row matched to a published concert
    for e in (coverage or {}).get("events") or []:
        cid, row = e.get("concert_id"), e.get("row")
        if not (e.get("found") and cid in by_id and isinstance(row, int)):
            continue
        # the report's row index comes from the CSV the Pipeline read; skip it when this
        # checkout's CSV holds another date at that index (CSV edited since)
        if not 0 <= row < len(reference) or reference[row]["date"] != local_day(by_id[cid]):
            continue
        matched.setdefault(cid, row)
        rows_used.add(row)
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
                "known": bool(lab.get("known")),
                "watch": c["id"] in matched,
            }
        )
    rated = {c["concert"]["id"] for c in cases}
    for cid in matched:
        if cid not in rated:
            cases.append({"concert": by_id[cid], "kind": "ref_matched", "label": "positive"})
    for i, row in enumerate(reference):
        if i not in rows_used:
            cases.append({"concert": row_concert(i, row), "kind": "ref_row", "label": "positive"})
    return cases


def local_day(concert: dict) -> str | None:
    try:
        return datetime.fromisoformat(concert["start"]).astimezone(TZ).date().isoformat()
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------- calls


def error_code(exc: llm.ModelError) -> str:
    """A ModelError message holds a model id and an HTTP status or an exception type only."""
    msg = str(exc)
    if m := re.search(r"HTTP (\d{3})", msg):
        return f"http_{m.group(1)}"
    return "timeout" if "Timeout" in msg else "transport"


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
    except llm.ModelError as exc:
        return {"data": None, "latency": None, "tin": 0, "tout": 0, "error": error_code(exc)}
    data = a.data
    if data is not None and (errors := judge.check_for(messages)(data)):
        data, a.reason = None, "unfaithful" if judge.UNFAITHFUL in errors else "check"
    return {
        "data": data,
        "latency": a.latency_s,
        "tin": a.tokens_in or 0,  # a provider may send null usage counts
        "tout": a.tokens_out or 0,
        "error": a.reason,
    }


def _h(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16)


def subsample(rated: list[tuple[dict, str]], share: float, seed: int) -> list[tuple[dict, str]]:
    """The first round(share × n) ratings of each label in a seeded hash order (nested: a third
    is inside two thirds for the same seed). Only the example pool shrinks; every case is still
    judged and scored (learning curve, WIP-81)."""
    if share >= 1:
        return rated
    out = []
    for label in ("liked", "disliked"):
        pool = sorted((r for r in rated if r[1] == label), key=lambda r: _h(seed, r[0].get("id")))
        out += pool[: round(share * len(pool))]
    return out


def run_condition(
    cand: dict, cond: str, cases: list[dict], artists: dict, profile: dict, workers: int
) -> list[dict]:
    spec = spec_of(cand)
    how = CONDITIONS[cond]
    rated = [(c["concert"], c["label"]) for c in cases if c["kind"] == "rated"]
    if how:
        rated = subsample(rated, how[1], how[2])

    lock, failed = threading.Lock(), [0]  # failed calls in a row

    def one(case: dict) -> dict:
        with lock:
            if failed[0] >= STOP_AFTER:
                return {"data": None, "latency": None, "tin": 0, "tout": 0, "error": "aborted"}
        if not how:
            ex = ()
        elif how[0] == "nearest":
            ex = judge.pick_nearest(case["concert"], rated, artists)
        else:
            ex = judge.pick_examples(case["concert"], rated)
        r = judge_one(spec, judge.messages_for(case["concert"], artists, profile, ex), client)
        with lock:
            bad = r["error"] in FAILED_CALL or str(r["error"]).startswith("http_")
            if r["error"] != "unfaithful":  # neither an outage nor a working call (as judging)
                failed[0] = failed[0] + 1 if bad else 0
        return r

    with httpx.Client() as client, ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, cases))


# ---------------------------------------------------------------- metrics


def wilson(k: int, n: int, z: float = 1.959963984540054) -> list[float] | None:
    if not n:
        return None
    p, d = k / n, 1 + z * z / n
    mid, half = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, mid - half), 3), round(min(1.0, mid + half), 3)]


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
    # ADR-0006: an unusable answer counts as "no" for recall and precision and ranks below every
    # valid answer for pairwise (which favours a candidate whose failures fall on disliked
    # concerts: pairwise on valid answers only is reported next to it, and validity is a gate)
    sc = [judge.score(a["data"]) if a["data"] else -1.0 for a in answers]
    ok = [a["data"] is not None for a in answers]
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
    if not any(ok):  # nothing measured: no quality figure at all, never a chance-level 50 %
        return {
            "n": len(cases), "valid": 0.0, "errors": errors, "recall": None,
            "recall_wilson95": None, "recall_with_discovery": None, "recall_by_kind": {},
            "precision": None, "precision_wilson95": None, "picked_rated": 0, "pairwise": None,
            "pairwise_valid": None, "verdicts": {}, "p50_s": None, "p95_s": None,
            "tokens_in": None, "tokens_out": None, "eur_per_1000": None, "cv": {}, "shown": None,
        }  # fmt: skip
    liked_r = [i for i in rated if cases[i]["label"] == "liked"]
    disliked_r = [i for i in rated if cases[i]["label"] == "disliked"]
    return {
        "n": len(cases),
        "valid": ratio(sum(ok), len(answers)),
        "errors": errors,
        "recall": ratio(len(picked), len(pos)),
        "recall_wilson95": wilson(len(picked), len(pos)),
        "recall_with_discovery": ratio(len(with_disc), len(pos)),
        "recall_by_kind": {
            k: [sum(verdict[i] in judge.PICKED for i in v), len(v)] for k, v in sub.items()
        },
        # ADR-0006: precision is 0 when nothing labelled is picked
        "precision": ratio(len(rated_liked_picked), len(rated_picked)) or 0.0,
        "precision_wilson95": wilson(len(rated_liked_picked), len(rated_picked)),
        "picked_rated": len(rated_picked),
        "pairwise": pairwise([sc[i] for i in liked_r], [sc[i] for i in disliked_r]),
        "pairwise_valid": pairwise(
            [sc[i] for i in liked_r if ok[i]], [sc[i] for i in disliked_r if ok[i]]
        ),
        "verdicts": {v: verdict.count(v) for v in (*judge.VERDICTS, None) if verdict.count(v)},
        "p50_s": round(statistics.median(lat), 2) if lat else None,
        "p95_s": round(lat[min(len(lat) - 1, math.ceil(0.95 * len(lat)) - 1)], 2) if lat else None,
        "tokens_in": round(statistics.mean(tin)) if tin else None,
        "tokens_out": round(statistics.mean(tout)) if tout else None,
        "eur_per_1000": cost,
        "cv": {f"{t:.0%}": cv_operating_point(cases, sc, t) for t in TARGET_RECALLS},
        "shown": shown_metrics(cases, answers),
    }


def cv_operating_point(cases: list[dict], sc: list[float], target: float) -> dict:
    """Two-fold cross-validated cut-off on the judge's score (WIP-81). Cases are split in two by a
    hash of their id; on each half the cut-off is the highest score whose recall on that half's
    positives reaches `target`; it decides the picks of the other half (every case tied at the
    cut-off is picked). Recall (positives) and precision (William's labels) are computed on these
    out-of-fold picks. The out-of-fold recall lands near `target` by construction, so the quality
    signal is the precision at that cut-off (ADR-0006, iteration 2). A cut-off that would reach
    an unusable answer (score -1, counted as "no") gives no figure."""
    none = {"recall": None, "recall_wilson95": None, "precision": None,
            "precision_wilson95": None, "picked_rated": 0, "cuts": []}  # fmt: skip
    fold = [_h("fold", c["concert"].get("id")) % 2 for c in cases]
    pos = [c["label"] in ("liked", "positive") for c in cases]
    picked = [False] * len(cases)
    cuts = []
    for f in (0, 1):
        train = sorted((sc[i] for i in range(len(cases)) if fold[i] != f and pos[i]), reverse=True)
        if not train:
            return none
        cut = train[math.ceil(target * len(train)) - 1]
        if cut < 0:
            return none
        cuts.append(cut_label(cut))
        for i in range(len(cases)):
            if fold[i] == f:
                picked[i] = sc[i] >= cut
    rated = [i for i, c in enumerate(cases) if c["kind"] == "rated"]
    rp = [i for i in rated if picked[i]]
    liked = sum(cases[i]["label"] == "liked" for i in rp)
    hits = sum(picked[i] for i in range(len(cases)) if pos[i])
    return {
        "recall": ratio(hits, sum(pos)),
        "recall_wilson95": wilson(hits, sum(pos)),
        "precision": ratio(liked, len(rp)) or 0.0,
        "precision_wilson95": wilson(liked, len(rp)),
        "picked_rated": len(rp),
        "cuts": cuts,
    }


def shown_metrics(cases: list[dict], answers: list[dict]) -> dict:
    """The production rule (judge.section, ADR-0006): recall of the concerts shown on the home
    page, the share of William's rated concerts shown, and the precision of each section."""
    # a rejected unfaithful reason leaves the concert unjudged: the page shows it in « Pour
    # toi » (« pas encore jugé », WIP-90), so it counts as shown there
    sec = ["pour_toi" if a.get("error") == "unfaithful" else judge.section(a["data"])
           for a in answers]  # fmt: skip
    pos = [i for i, c in enumerate(cases) if c["label"] in ("liked", "positive")]
    rated = [i for i, c in enumerate(cases) if c["kind"] == "rated"]
    shown = [i for i in rated if sec[i] != "tout_voir"]
    hits = sum(sec[i] != "tout_voir" for i in pos)
    per = {}
    for name in ("ne_pas_rater", "pour_toi", "decouvertes", "tout_voir"):
        ids = [i for i in rated if sec[i] == name]
        liked = sum(cases[i]["label"] == "liked" for i in ids)
        per[name] = {"n": len(ids), "liked": liked, "precision": ratio(liked, len(ids)),
                     "wilson95": wilson(liked, len(ids))}  # fmt: skip
    # WIP-88: a known artist (scoring.js isKnownMatch on the leave-one-out profile, so a case's
    # own like never counts) goes to "À ne pas rater" whatever the judge says. Rule matches exist
    # for William's rated concerts only, so reference-only positives are judge-only here.
    known = [c.get("kind") == "rated" and bool(c.get("known")) for c in cases]
    hits_k = sum(known[i] or sec[i] != "tout_voir" for i in pos)
    shown_k = [i for i in rated if known[i] or sec[i] != "tout_voir"]
    ov = [i for i in rated if known[i]]
    ov_liked = sum(cases[i]["label"] == "liked" for i in ov)
    top = [i for i in rated if known[i] or sec[i] == "ne_pas_rater"]  # the page's section
    top_liked = sum(cases[i]["label"] == "liked" for i in top)
    return {
        "recall": ratio(hits, len(pos)),
        "recall_wilson95": wilson(hits, len(pos)),
        "share_shown": ratio(len(shown), len(rated)),
        "sections": per,
        "with_known": {
            "recall": ratio(hits_k, len(pos)),
            "recall_wilson95": wilson(hits_k, len(pos)),
            "share_shown": ratio(len(shown_k), len(rated)),
            "ne_pas_rater": {
                "n": len(top),
                "liked": top_liked,
                "precision": ratio(top_liked, len(top)),
                "wilson95": wilson(top_liked, len(top)),
            },  # fmt: skip
            "overrides": {
                "n": len(ov),
                "liked": ov_liked,
                "precision": ratio(ov_liked, len(ov)),
                "moved_from_tout_voir": sum(sec[i] == "tout_voir" for i in ov),
            },
        },  # fmt: skip
    }


def cut_label(score: float) -> str:
    """A cut-off score as the answer it stands for, e.g. "discovery ≥ 70" or "no ≤ 40"."""
    rank = min(int(score), 3)
    verdict = {v: k for k, v in judge.VERDICT_RANK.items()}[rank]
    conf = round((score - rank) * 101)
    return f"no ≤ {100 - conf}" if verdict == "no" else f"{verdict} ≥ {conf}"


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


def errs(m: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(m["errors"].items())) or "none"


def compact(cid: str, cond: str, m: dict) -> str:
    return (
        f"judge_taste {cid} [{cond}]: recall {pct(m['recall'])}, precision "
        f"{pct(m['precision'])} on {m['picked_rated']} picked, pairwise {pct(m['pairwise'])} "
        f"(valid only {pct(m['pairwise_valid'])}), valid {pct(m['valid'])} (errors: {errs(m)}), "
        f"p95 {m['p95_s']} s, €{m['eur_per_1000']} / 1,000{shown_text(m)}{cv_text(m)}"
    )


def shown_text(m: dict) -> str:
    sh = m.get("shown")
    if not sh:
        return ""
    sec = "; ".join(
        f"{k} {pct(v['precision'])} on {v['n']}" for k, v in sh["sections"].items() if v["n"]
    )
    k = sh.get("with_known") or {}
    ov = k.get("overrides") or {}
    top = k.get("ne_pas_rater") or {}
    known = (
        f"; with known artists (WIP-88): recall {pct(k.get('recall'))}"
        f"{ci(k.get('recall_wilson95'))}, {pct(k.get('share_shown'))} shown, overrides "
        f"{ov.get('liked')}/{ov.get('n')} liked ({ov.get('moved_from_tout_voir')} from tout_voir), "
        f"ne_pas_rater {pct(top.get('precision'))}{ci(top.get('wilson95'))} on {top.get('n')}"
        if k
        else ""
    )
    return (
        f"; shown (ADR-0006 rule): recall {pct(sh['recall'])}{ci(sh['recall_wilson95'])}, "
        f"{pct(sh['share_shown'])} of rated concerts shown; precision by section: {sec}{known}"
    )


def cv_text(m: dict) -> str:
    """Cross-validated operating points (WIP-81): out-of-fold recall and precision."""
    parts = [
        f"@{t}: precision {pct(p['precision'])}{ci(p['precision_wilson95'])} on "
        f"{p['picked_rated']}, recall {pct(p['recall'])}{ci(p['recall_wilson95'])}, cut "
        f"{' / '.join(p['cuts']) or 'n/a'}"
        for t, p in (m.get("cv") or {}).items()
    ]
    return f"; cut-off cross-validated {' / '.join(parts)}" if parts else ""


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
        "(95 % CI) | Pairwise (valid only) | Valid (errors) | p95 s | Tokens in / out | "
        "€ / 1,000 | Verdicts | Cross-validated cut-offs |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cid, cond, m in rows:
        verdicts = ", ".join(f"{k or 'invalid'} {v}" for k, v in m["verdicts"].items())
        out.append(
            f"| {cid} | {cond} | {pct(m['recall'])}{ci(m['recall_wilson95'])} | "
            f"{pct(m['recall_with_discovery'])} | {pct(m['precision'])}"
            f"{ci(m['precision_wilson95'])} on {m['picked_rated']} | {pct(m['pairwise'])} "
            f"({pct(m['pairwise_valid'])}) | {pct(m['valid'])} ({errs(m)}) | {m['p95_s']} | "
            f"{m['tokens_in']} / {m['tokens_out']} | {m['eur_per_1000']} | {verdicts} | "
            f"{cv_text(m).removeprefix('; cut-off cross-validated ') or 'n/a'} |"
        )
    for cid, why in skipped.items():
        out.append(f"| {cid} | — | skipped: {why} | | | | | | | | | |")
    out += [
        "",
        "Recall by subset (picked / n): "
        + "; ".join(
            f"{cid} [{cond}] "
            + ", ".join(f"{k} {a}/{b}" for k, (a, b) in m["recall_by_kind"].items())
            for cid, cond, m in rows
            if m["recall_by_kind"]
        ),
        "",
    ]
    return "\n".join(out)


GATE_CONDITION = "nn"  # the production setup: nearest ratings, all of them (ADR-0006)


def gate(results: dict, skipped: dict, conds: list[str], only: str) -> int:
    """Quality gate on the routed model (config/models.yaml tasks.judge_taste.min_quality):
    in condition GATE_CONDITION, the recall of the concerts judge.section() shows. 1 when it is
    below the bar or was not measured; 0 when this run left the routed model or the condition
    out (--only, --conditions) or no task is routed."""
    tasks = llm.load_tasks(CONFIG)
    task = tasks.get("judge_taste")
    if task is None or task.min_quality is None or GATE_CONDITION not in conds:
        return 0
    p = task.primary
    cands = yaml.safe_load((ROOT / "models.yaml").read_text("utf-8"))["judge_taste"]
    want = (p.provider, p.model, p.extra or {})
    routed = next(
        (
            c
            for c in cands
            if not c.get("retired")
            and (c.get("provider"), c.get("model"), c.get("extra") or {}) == want
        ),
        None,
    )
    if routed is None:
        annotate("error", "Judge eval: no candidate matches the routed judge_taste model")
        return 1
    if only and routed["id"] not in {i.strip() for i in only.split(",")}:
        return 0
    key = f"{routed['id']} [{GATE_CONDITION}]"
    shown = (results.get(key) or {}).get("shown")
    if not shown or shown.get("recall") is None:
        why = skipped.get(key) or skipped.get(routed["id"]) or "no result"
        annotate("error", f"Judge eval gate: {key} not measured ({why})")
        return 1
    if shown["recall"] < task.min_quality:
        annotate("error", f"Judge eval gate: {key} shown recall {pct(shown['recall'])} < "
                 f"{pct(task.min_quality)}")  # fmt: skip
        return 1
    return 0


def candidates(only: str) -> list[dict]:
    """Callable candidates of eval/judge/models.yaml; any other provider is a config error."""
    cands = yaml.safe_load((ROOT / "models.yaml").read_text("utf-8"))["judge_taste"]
    cands = [c for c in cands if not c.get("retired") and c["provider"] != "none"]
    if bad := [c["id"] for c in cands if c["provider"] not in EU_PROVIDERS]:
        raise ValueError(f"provider not allowed for personal data (ADR-0006): {', '.join(bad)}")
    if only:
        wanted = {i.strip() for i in only.split(",") if i.strip()}
        if unknown := sorted(wanted - {c["id"] for c in cands}):
            annotate("warning", f"Judge eval: --only: unknown candidate id(s) {', '.join(unknown)}")
        cands = [c for c in cands if c["id"] in wanted]
    return cands


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="python -m eval.judge")
    ap.add_argument("--site", default=os.environ.get("TASTE_SITE", "site"))
    ap.add_argument("--only", default=os.environ.get("JUDGE_ONLY", ""))
    ap.add_argument("--conditions", default=",".join(DEFAULT_CONDITIONS))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-calls", type=int, default=2500, help="cost guard for the whole run")
    ap.add_argument(
        "--allow-empty-taste",
        action="store_true",
        help="call the models even when the written taste is empty (WIP-78: off by default)",
    )
    # eval/results/ is git-ignored; the workflow uploads the file (aggregates only)
    ap.add_argument("--results", default=str(ROOT.parent / "results" / "judge.json"))
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
    try:
        ids = [c["concert"]["id"] for c in cases if c["kind"] != "ref_row"]
        descriptions = load_descriptions(url, ids)
    except Exception as exc:  # the type only; the eval runs on without descriptions
        annotate("warning", f"Judge eval: descriptions unavailable ({type(exc).__name__})")
        descriptions = {}
    for c in cases:
        if text := descriptions.get(c["concert"]["id"]):
            c["concert"] = {**c["concert"], "description": text}
    with_desc = sum(bool(c["concert"].get("description")) for c in cases)
    ref = references(cases)
    taste_len = len(profile.get("taste_text") or "") if isinstance(profile, dict) else 0
    annotate(
        "notice",
        f"Judge eval: {len(cases)} cases, {ref['labels']} labels ({ref['liked']} liked, "
        f"{ref['disliked']} disliked), {ref['positives']} positives, {with_desc} with the "
        f"listing's description; written taste {taste_len} "
        f"chars; site data {site['generated_at']}. References on the same labels: rule-based "
        f"pairwise {pct(ref['rule_pairwise'])}; former watch {ref['watch_liked']}/"
        f"{ref['watch_rated']} of its rated picks liked",
    )

    if not taste_len and not args.allow_empty_taste:
        # the written taste is the main input of FR-5: without it a run measures nothing the
        # decision needs (runs 37738954308, 37740875208, 37745152264), so no paid call (WIP-78)
        annotate("notice", "Judge eval: skipped: the written taste is empty in the event store")
        return 0
    rows, skipped, results, calls, failed = [], {}, {}, 0, False
    try:
        for cand in candidates(args.only):
            ok, why = spec_of(cand).available()
            if not ok:
                skipped[cand["id"]] = why
                continue
            # GitHub keeps about 10 notices per step (run 37738954308 showed 10 of 11): one
            # annotation per candidate, both conditions in it, plus the opening one
            lines, level = [], "notice"
            for cond in conds:
                key = f"{cand['id']} [{cond}]"
                if calls + len(cases) > args.max_calls:
                    skipped[key] = f"cost guard ({args.max_calls} calls)"
                    continue
                calls += len(cases)
                log.info("candidate %s, condition %s", cand["id"], cond)
                try:
                    answers = run_condition(
                        cand, cond, cases, site["artists"], profile, args.workers
                    )
                    m = metrics(cand, cases, answers)
                except Exception as exc:  # one pair never stops the others; type only
                    skipped[key] = f"error: {type(exc).__name__}"
                    # in the candidate's annotation, not a separate one (per-step cap, WIP-77)
                    lines.append(f"judge_taste {key}: failed ({type(exc).__name__})")
                    level = "error"
                    failed = True
                    continue
                lines.append(compact(cand["id"], cond, m))
                if m["valid"] != 1 and level == "notice":
                    level = "warning"
                failed |= not m["valid"]  # not one usable answer: the run fails
                rows.append((cand["id"], cond, m))
                results[key] = m
            if lines:
                annotate(level, "\n".join(lines))
    finally:  # the summary is written even if the loop stops (paid calls stay accounted)
        md = table(ref, rows, skipped)
        print(md)
        if path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(md)
        out = Path(args.results)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"references": ref, "results": results, "skipped": skipped}, indent=1),
            "utf-8",
        )
    return 1 if gate(results, skipped, conds, args.only) or failed else 0


if __name__ == "__main__":
    sys.exit(main())
