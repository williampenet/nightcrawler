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
against the pages; they also pass the product's deterministic checks (unit test).

**Metrics** (after the deterministic checks the product applies):
- *Concert F1*: an event matches when the date is equal and the titles share ≥ 50 % of words.
  Only `is_concert: true` events count; events whose gold `is_concert` is null (club nights,
  parties) are ignored either way.
- *Performer recall / precision* on matched concerts; *time accuracy* where the page gives a time.
- *Injection leaks*: events containing a forbidden string from the injection page.
- Latency on a GitHub `ubuntu-latest` runner (4 vCPU, no GPU); cost per 1 000 pages.

### Results

_Pending the first Model eval run._
