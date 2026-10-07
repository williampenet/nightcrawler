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
import shutil
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
    if cand.get("retired"):
        return {"skipped": "retired"}
    spec = llm.ModelSpec.from_dict(cand)
    ok, why = spec.available()
    if not ok:
        return {"skipped": why}
    if spec.provider == "local":
        binary = os.environ.get("LLAMA_SERVER")
        if not binary:
            return {"skipped": "LLAMA_SERVER not set (local models run in the Model eval workflow)"}
        cached = cand.get("cache", True)
        cache_dir = Path(
            os.environ.get("MODEL_CACHE", ".cache/models")
            if cached
            else os.environ.get("MODEL_SCRATCH", ".cache/scratch-models")
        )
        if not (cache_dir / (spec.sha256 or "")[:16] / (spec.file or "")).is_file():
            if why := low_disk(cache_dir, cand.get("size_bytes") or 0):
                return {"skipped": why}
        try:
            weights = llm.ensure_weights(spec, cache_dir)
            with llm.LlamaServer(binary, weights) as server:
                spec.base_url = server.base_url
                return {"cases": [ask(spec, c, task) for c in cases]}
        finally:
            if not cached:  # free the disk for the next candidates; never enters the cache
                shutil.rmtree(cache_dir, ignore_errors=True)
    return {"cases": [ask(spec, c, task) for c in cases]}


DISK_MARGIN_BYTES = 2 * 1024**3


def low_disk(directory: Path, size_bytes: int) -> str | None:
    """Skip reason when the weights (plus a margin) would not fit on the disk, else None."""
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    need = size_bytes + DISK_MARGIN_BYTES
    if free < need:
        return f"disk ({free / 1e9:.1f} GB free < {need / 1e9:.1f} GB needed)"
    return None


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
        "attempts": 0,
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
        attempts=a.attempts,
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
    # tokens averaged over answered pages, so a failed call does not lower the cost estimate
    answered = [r for r in rows if r["latency_s"] is not None]
    tin = sum(r["tokens_in"] for r in answered) / len(answered) if answered else 0
    tout = sum(r["tokens_out"] for r in answered) / len(answered) if answered else 0
    price = cand.get("price_eur_per_mtok")
    if not answered:
        cost = None  # no page answered: no measured cost (shown as n/a, never €0)
    elif price:
        cost = round((tin * price["in"] + tout * price["out"]) / 1000, 2)
    else:
        cost = 0.0 if cand["provider"] == "local" else None
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
        # transport: pages with no answer (ModelError) and HTTP retries (429 / 5xx)
        "failed_calls": sum(1 for r in rows if r["latency_s"] is None and r["errors"]),
        "retries": sum(max(0, r.get("attempts", 0) - 1) for r in rows),
        "per_case": {r["id"]: score_case(r["checked"], by_id[r["id"]]["expected"]) for r in rows},
    }


def table(cands: list[dict], results: dict) -> str:
    head = (
        "| Candidate | Hosting | Licence | Concert F1 | Precision | Recall | Performer recall"
        " | Performer precision | Time acc. | Schema-valid | Injection leaks | p50 / p95 latency"
        " | Tokens in / out (avg / page) | Failed calls / retries | Cost / 1 000 pages |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for c in cands:
        r = results[c["id"]]
        if "skipped" in r:
            lines.append(
                f"| {c['id']} | {c.get('hosting', '')} | {c.get('licence', '')} | "
                f"skipped: {c.get('note') or r['skipped']} |||||||||||| "
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
            f"{k['injected_events']} | {lat} | {m['tokens_in_avg']} / {m['tokens_out_avg']} | "
            f"{m['failed_calls']} / {m['retries']} | {cost} |"
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
        f"p95 {m['latency_p95_s']}s in {m['tokens_in_avg']} out {m['tokens_out_avg']} tok "
        f"cost/1000 {m['cost_eur_per_1000']} EUR failed {m['failed_calls']} "
        f"retries {m['retries']} | {cases}"
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


ROUTED = "routed"  # `--only routed`: the candidate config/models.yaml routes the task to


def routed_candidate(cands: list[dict], task: llm.Task) -> dict | None:
    """The eval candidate that sends exactly the routed request (provider, model and extra)."""
    p = task.primary
    for c in cands:
        if (c.get("provider"), c.get("model"), c.get("extra") or {}) == (
            p.provider,
            p.model,
            p.extra or {},
        ) and not c.get("retired"):
            return c
    return None


def gate(task: llm.Task, routed: dict | None, cands: list[dict], results: dict) -> int:
    """Quality gate on the routed model (task.min_quality): 1 when it fails or was not measured."""
    if task.min_quality is None:
        return 0
    if routed is None:
        annotate("error", f"task {task.name}: no eval candidate matches the routed model")
        return 1
    if routed["id"] not in {c["id"] for c in cands}:
        return 0  # --only left the routed model out: nothing to gate in this run
    m = results.get(routed["id"], {}).get("metrics")
    if not m:
        why = results.get(routed["id"], {}).get("skipped", "no result")
        annotate("error", f"{routed['id']} (routed): not measured ({why})")
        return 1
    if m["checked"]["f1"] < task.min_quality:
        annotate("error", f"{routed['id']}: F1 {m['checked']['f1']} < {task.min_quality}")
        return 1
    if m["checked"]["injected_events"]:
        annotate("error", f"{routed['id']}: {m['checked']['injected_events']} injected event(s)")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval")
    ap.add_argument("--task", default="extract_events")
    ap.add_argument(
        "--only", default="", help=f"comma-separated candidate ids; '{ROUTED}' = the routed one"
    )
    ap.add_argument("--results-dir", default=str(ROOT / "results"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cands = yaml.safe_load((ROOT / "models.yaml").read_text("utf-8"))[args.task]
    task = llm.load_tasks(ROOT.parent / "config/models.yaml")[args.task]
    routed = routed_candidate(cands, task)
    if args.only:
        wanted = {i.strip() for i in args.only.split(",") if i.strip()}
        if ROUTED in wanted:
            wanted.discard(ROUTED)
            if routed:
                wanted.add(routed["id"])
            else:
                annotate("error", f"--only {ROUTED}: no candidate matches the routed model")
                return 1
        if unknown := sorted(wanted - {c["id"] for c in cands}):
            log.warning("unknown candidate id(s): %s", ", ".join(unknown))
            annotate("warning", f"--only: unknown candidate id(s): {', '.join(unknown)}")
        cands = [c for c in cands if c["id"] in wanted]
    cases = load_cases(args.task)

    # hosted first: a long or failing local CPU run must not delay or block them
    order = sorted(cands, key=lambda c: c["provider"] == "local")
    results: dict[str, dict] = {}
    for cand in order:
        log.info("candidate %s", cand["id"])
        try:
            res = run_candidate(cand, cases, task)
        except llm.ModelError as exc:
            # ModelError messages hold only a model id, an HTTP status or an exception type
            res = {"skipped": f"error: {exc}"[:200]}
        except Exception as exc:  # one candidate never aborts the others
            res = {"skipped": f"error: {type(exc).__name__}"}
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
    out = Path(args.results_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), "utf-8")

    # quality gate on the model the product routes to, once its ADR sets a threshold
    return gate(task, routed, cands, results)


if __name__ == "__main__":
    sys.exit(main())
