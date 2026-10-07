# eval/

Model evaluation harness. Only for products that call an LLM at runtime; delete the folder otherwise.

## Files
- `cases.jsonl` — one test case per line: `{"id": "...", "input": ..., "expected": ..., "tags": ["nominal" | "edge" | "injection"]}`
- `models.yaml` — candidates to compare per task (provider, model id, pinned revision, hosting). Same format as `config/models.yaml`, so the product and the eval call models the same way.
- Cases are grouped by task (`"task": "categorize"`); each task is evaluated separately.
- runner — `npm run eval` (Node) or `python -m eval` (Python); writes `eval/results/latest.json` and prints a markdown table for `docs/MODEL_EVAL.md`.

## Runner contract
1. Load `models.yaml`; skip a hosted candidate whose API key env var is missing, and say so.
2. For each case × model: call through the product's provider abstraction, record output, latency, tokens.
3. Score with a deterministic scorer (exact match, schema validation, rule-based rubric).
4. If the task has a `fallback`, also run the routed pipeline (primary → fallback on validation failure) and report: escalation rate, quality, p95 latency and cost of the pipeline vs primary alone vs fallback alone.
5. Exit non-zero if the chosen model's quality is below `EVAL_MIN_QUALITY` (from the model-selection ADR).

No real personal data in `cases.jsonl`. Results are committed only as the summary table in `docs/MODEL_EVAL.md`.

## Nightcrawler
- Task `extract_events`: 7 pages / 72 events, see `docs/MODEL_EVAL.md` for the set and the metrics.
- Real pages were captured on 2026-10-05 (visible text, lightly trimmed); `today` is fixed in each case
  so the set stays valid over time.
- `provider: gold` replays the gold labels (the proprietary baseline that wrote them).
- `provider: scaleway` candidates (WIP-63) run when `SCW_GENAI_SECRET_KEY` is set, otherwise the row
  reads "skipped: no key"; `retired: true` keeps a row without calling it. Run a subset with
  `python -m eval --only id,id` or the workflow's `only` input. The job summary shows, per
  candidate, F1 / precision / recall, injection leaks, p50 / p95 latency, average tokens in / out,
  failed calls / retries and the cost per 1 000 pages computed from the measured tokens.
