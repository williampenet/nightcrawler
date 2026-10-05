"""Model evaluation runner: `python -m eval [--task extract_events] [--only id,id]`.

Calls every candidate of `eval/models.yaml` through the product's provider abstraction
(`nightcrawler.llm`), scores the answers deterministically, writes `eval/results/latest.json`
and prints the markdown table for `docs/MODEL_EVAL.md`. Local candidates need LLAMA_SERVER
(path to llama.cpp's `llama-server`); hosted candidates need their API key. Others are skipped.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from datetime import date
from pathlib import Path

import httpx
import yaml

from nightcrawler import extract, llm
from nightcrawler.cli import annotate

from .score import score_case, summarize

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("eval")


def load_cases(task: str) -> list[dict]:
    rows = [
        json.loads(line)
        for line in (ROOT / "cases.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    return [r for r in rows if r["task"] == task]


def run_candidate(cand: dict, cases: list[dict], task: llm.Task) -> dict:
    """Per-case raw and checked predictions for one candidate, or a skip reason."""
    if cand["provider"] == "gold":
        return {
            "cases": [
                {
                    "id": c["id"],
                    "raw": c["expected"]["events"],
                    "checked": c["expected"]["events"],
                    "latency_s": None,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "valid": True,
                    "errors": [],
                }
                for c in cases
            ]
        }
    spec = llm.ModelSpec.from_dict(cand)
    ok, why = spec.available()
    if not ok:
        return {"skipped": why}
    if spec.provider == "local":
        binary = os.environ.get("LLAMA_SERVER")
        if not binary:
            return {"skipped": "LLAMA_SERVER not set (local models run in the Model eval workflow)"}
        weights = llm.ensure_weights(spec, os.environ.get("MODEL_CACHE", ".cache/models"))
        with llm.LlamaServer(binary, weights) as server:
            spec.base_url = server.base_url
            return {"cases": [ask(spec, c, task) for c in cases]}
    return {"cases": [ask(spec, c, task) for c in cases]}


def ask(spec: llm.ModelSpec, case: dict, task: llm.Task) -> dict:
    inp = case["input"]
    today = date.fromisoformat(inp["today"])
    out = {
        "id": case["id"],
        "raw": [],
        "checked": [],
        "latency_s": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "valid": False,
        "errors": [],
    }
    try:
        with httpx.Client() as client:
            a = llm.chat_json(
                spec,
                extract.messages_for(inp["text"], today, inp["venue"]),
                extract.SCHEMA,
                max_tokens=task.max_output_tokens,
                temperature=task.temperature,
                timeout_s=task.timeout_s,
                client=client,
            )
    except llm.ModelError as exc:
        out["errors"] = [str(exc)]
        return out
    out.update(
        latency_s=round(a.latency_s, 2),
        tokens_in=a.tokens_in,
        tokens_out=a.tokens_out,
        errors=a.errors[:3],
        valid=a.data is not None,
    )
    if a.data is not None:
        out["raw"] = a.data["events"]
        out["checked"], rejected = extract.check_events(a.data, inp["text"], today)
        out["rejected"] = [n for n in rejected if n != "date corrected"]
        out["corrected"] = rejected.count("date corrected")
        out["layout"] = a.data.get("layout")
    return out


def metrics(cand: dict, res: dict, cases: list[dict]) -> dict:
    by_id = {c["id"]: c for c in cases}
    rows = res["cases"]
    raw = summarize([score_case(r["raw"], by_id[r["id"]]["expected"]) for r in rows])
    checked = summarize([score_case(r["checked"], by_id[r["id"]]["expected"]) for r in rows])
    lat = sorted(r["latency_s"] for r in rows if r["latency_s"] is not None)
    tin = sum(r["tokens_in"] for r in rows) / len(rows)
    tout = sum(r["tokens_out"] for r in rows) / len(rows)
    price = cand.get("price_eur_per_mtok")
    cost = (
        round((tin * price["in"] + tout * price["out"]) / 1000, 2)
        if price
        else (0.0 if cand["provider"] == "local" else None)
    )
    return {
        "id": cand["id"],
        "checked": checked,
        "raw": raw,
        "valid_rate": round(sum(r["valid"] for r in rows) / len(rows), 3),
        "latency_p50_s": round(statistics.median(lat), 1) if lat else None,
        "latency_p95_s": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None,
        "tokens_in_avg": round(tin),
        "tokens_out_avg": round(tout),
        "cost_eur_per_1000": cost,
        "per_case": {r["id"]: score_case(r["checked"], by_id[r["id"]]["expected"]) for r in rows},
    }


def table(cands: list[dict], results: dict) -> str:
    head = (
        "| Candidate | Hosting | Licence | Concert F1 | Precision | Recall | Performer recall"
        " | Performer precision | Time acc. | Schema-valid | Injection leaks | p50 / p95 latency"
        " | Cost / 1 000 pages |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for c in cands:
        r = results[c["id"]]
        if "skipped" in r:
            lines.append(
                f"| {c['id']} | {c.get('hosting', '')} | {c.get('licence', '')} | "
                f"skipped: {c.get('note') or r['skipped']} |||||||||| "
            )
            continue
        m, k = r["metrics"], r["metrics"]["checked"]
        lat = (
            f"{m['latency_p50_s']} s / {m['latency_p95_s']} s"
            if m["latency_p50_s"] is not None
            else "n/a"
        )
        cost = "n/a" if m["cost_eur_per_1000"] is None else f"€{m['cost_eur_per_1000']}"
        lines.append(
            f"| {c['id']} | {c.get('hosting', '')} | {c.get('licence', '')} | **{k['f1']}** | "
            f"{k['precision']} | {k['recall']} | {k['performer_recall']} | "
            f"{k['performer_precision']} | {k['time_accuracy']} | {m['valid_rate']} | "
            f"{k['injected_events']} | {lat} | {cost} |"
        )
    return head + "\n".join(lines) + "\n"


def compact(m: dict) -> str:
    k = m["checked"]
    cases = " ".join(
        f"{cid}:{c['tp']}/{c['tp'] + c['fn']}+{c['fp']}fp" for cid, c in m["per_case"].items()
    )
    return (
        f"{m['id']}: F1 {k['f1']} (raw {m['raw']['f1']}) P {k['precision']} R {k['recall']} "
        f"perf R {k['performer_recall']} P {k['performer_precision']} time {k['time_accuracy']} "
        f"valid {m['valid_rate']} leaks {k['injected_events']} p50 {m['latency_p50_s']}s "
        f"p95 {m['latency_p95_s']}s in {m['tokens_in_avg']} out {m['tokens_out_avg']} tok | "
        f"{cases}"
    )


def misses(res: dict, cases: list[dict]) -> str:
    """Short list of wrong events, for reading the eval from annotations."""
    from .score import match

    out = []
    by_id = {c["id"]: c for c in cases}
    for r in res["cases"]:
        gold = by_id[r["id"]]["expected"]["events"]
        pred = r["checked"]
        pairs = dict(match(pred, gold))
        fps = [
            f"{p['date']} {p['title'][:30]}{'' if p['is_concert'] else ' (not concert)'}"
            for i, p in enumerate(pred)
            if i not in pairs
        ]
        hit = set(pairs.values())
        fns = [f"{g['date']} {g['title'][:30]}" for j, g in enumerate(gold) if j not in hit]
        if fps or fns or r["errors"]:
            out.append(
                f"{r['id']}: extra=[{'; '.join(fps)[:300]}] missed=[{'; '.join(fns)[:300]}]"
                f" err={'; '.join(r['errors'])[:150]} rejected={r.get('rejected', [])[:5]}"
                f" corrected={r.get('corrected', 0)} layout={r.get('layout')}"
            )
    return "\n".join(out) or "no misses"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval")
    ap.add_argument("--task", default="extract_events")
    ap.add_argument("--only", default="", help="comma-separated candidate ids")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cands = yaml.safe_load((ROOT / "models.yaml").read_text("utf-8"))[args.task]
    if args.only:
        wanted = set(args.only.split(","))
        cands = [c for c in cands if c["id"] in wanted]
    cases = load_cases(args.task)
    task = llm.load_tasks(ROOT.parent / "config/models.yaml")[args.task]

    results: dict[str, dict] = {}
    for cand in cands:
        log.info("candidate %s", cand["id"])
        try:
            res = run_candidate(cand, cases, task)
        except llm.ModelError as exc:
            res = {"skipped": f"error: {exc}"[:200]}
        if "cases" in res:
            res["metrics"] = metrics(cand, res, cases)
            annotate("notice", compact(res["metrics"]))
            if cand["provider"] != "gold":
                annotate("warning", f"{cand['id']} misses:\n{misses(res, cases)}")
        else:
            annotate("notice", f"{cand['id']}: skipped ({res['skipped']})")
        results[cand["id"]] = res

    md = f"### Model eval — task `{args.task}` ({len(cases)} pages)\n\n" + table(cands, results)
    print(md)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(md)
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "latest.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")

    # quality gate on the model the product routes to, once its ADR sets a threshold
    if task.min_quality is not None:
        chosen = next((c["id"] for c in cands if c.get("model") == task.primary.model), None)
        m = results.get(chosen, {}).get("metrics")
        if m and m["checked"]["f1"] < task.min_quality:
            annotate("error", f"{chosen}: F1 {m['checked']['f1']} < {task.min_quality}")
            return 1
        if m and m["checked"]["injected_events"]:
            annotate("error", f"{chosen}: {m['checked']['injected_events']} injected event(s)")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
