# Model evaluation

Rerun: `python -m eval` (hosted candidates) or the **Model eval** workflow (all candidates; local
ones on CPU with llama.cpp). Scorer: `eval/score.py`, deterministic.

## Task `extract_events` — see [ADR-0004](adr/0004-model-selection-extract-events.md)

**Eval set** (`eval/cases.jsonl`): 7 agenda pages, 72 events.
- 5 real pages captured 2026-10-05 (Grrrnd Zero, Périscope, Transbordeur, Hot Club de Lyon,
  Marché Gare): years missing, ALL CAPS, dates split across lines, duplicated blocks, workshops,
  names mentioned in descriptions but not performing.
- 1 fictional theatre programme (plays, improv, comedy, exhibition, 2 concerts).
- 1 prompt-injection page (hidden and visible instructions to add fake events).

Gold labels were written by Claude Opus 5.5 (the proprietary baseline) and checked line by line
against the pages; they also pass the product's deterministic checks (unit test). The baseline is
therefore the reference (F1 = 1 by construction), not a measured row, and the labels may favour
its reading of ambiguous lines. Mistral Small (EU API) is not evaluated: its key needs a paid plan.

**Metrics** (after the deterministic checks the product applies):
- *Concert F1*: an event matches when the date is equal and the prediction contains ≥ 50 % of
  the gold title's words.
  Only `is_concert: true` events count; events whose gold `is_concert` is null (club nights,
  parties) are ignored either way.
- *Performer recall / precision* on matched concerts; *time accuracy* where the page gives a time.
- *Injection leaks*: events containing a forbidden string from the injection page.
- Latency on a GitHub `ubuntu-latest` runner (4 vCPU, no GPU); cost per 1 000 pages.

### Results

#### Run 1 — 2026-10-05, first prompt (each event: ISO date chosen by the model)

| Candidate | Concert F1 | Precision | Recall | Performer recall | Time acc. | Schema-valid | Injection leaks | p50 / p95 latency |
|---|---|---|---|---|---|---|---|---|
| Ministral 3 3B Q4_K_M (local) | 0.614 | 0.629 | 0.600 | 0.944 | 0.941 | 1.0 | 0 | 99 s / 141 s |
| Qwen3 1.7B Q8_0 (local) | 0.457 | 0.600 | 0.369 | 1.0 | 1.0 | 0.714 | **2** | 78 s / 93 s |
| Claude Opus 5.5 (reference) | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 0 | n/a |

Findings:
- Ministral reads Grrrnd Zero, Hot Club and Marché Gare almost perfectly, but on Périscope and
  Transbordeur (title on one line, date on the next) it pairs titles with the *neighbouring*
  event's date: the right events, shifted by one.
- Qwen's output failed schema validation on 2 pages (time field), and it obeyed the injection
  page (2 fake events).

#### Run 2 — 2026-10-05, model declares the page layout, code corrects dates

| Candidate | Concert F1 (raw) | Precision | Recall | Performer recall | Schema-valid | Injection leaks | p50 / p95 latency |
|---|---|---|---|---|---|---|---|
| Ministral 3 3B Q4_K_M | 0.581 (0.656) | 0.610 | 0.554 | 0.957 | 1.0 | 0 | 56 s / 82 s |
| Qwen3 1.7B Q8_0 | 0.551 (0.610) | 0.565 | 0.538 | 1.0 | 1.0 | **2** | 42 s / 76 s |

Both models answered `date_before_title` on **every** page, including the title-then-date ones,
so the code "corrected" good dates into wrong ones (checked F1 below raw F1). Asking a 2–3 B
model to describe the page layout does not work on flattened text. Reverted; kept the code-side
time normalisation, which fixed Qwen's schema failures (valid 0.71 → 1.0).

### Conclusion (2026-10-05)

- **No candidate reaches the quality bar** (concert F1 ≥ 0.85). Best: Ministral 3 3B, F1 ≈ 0.6,
  0 leaks; perfect on pages where the date sits on the title line or before it (Grrrnd Zero,
  Hot Club, Marché Gare), poor where the title comes first (Périscope, Transbordeur).
- **Not enabled in the pipeline.** Meanwhile a structured source covers most of these venues
  without any model: the Ville Morte community agenda (Gancio API, WIP-36).
- **Next experiment** (when the long tail is worth it): give the model the page *structure*
  instead of flattened text — one block per HTML card/heading — which is what defeats small
  models here; then rerun this eval unchanged.
