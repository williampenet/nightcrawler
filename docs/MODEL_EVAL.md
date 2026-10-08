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
its reading of ambiguous lines. Mistral Small 3.2 is evaluated on Scaleway from run 3 (its Mistral
API id is retired). Of the 72 labelled events, 65 are concerts and count in the F1.

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

#### Run 3 — 2026-10-07, WIP-63 stronger candidates (same prompt, set, scorer and bar)

Source: annotations of the Model eval workflow
[run 37591806394](https://github.com/williampenet/nightcrawler/actions/runs/37591806394) (push to
`main` at 39768de; llama.cpp b11425, 4 CPUs, 15 GiB RAM as reported by `free -g` (≈ 16.1 GB),
86 GB free disk). Candidates added in
`eval/models.yaml` (sources in [ADR-0004](adr/0004-model-selection-extract-events.md), amendment
2026-10-07). Hosted ones on Scaleway Generative APIs (Paris), `reasoning_effort: none` for Gemma
and Qwen. 7 pages, 65 gold concert events scored.

| Candidate | Hosting | Concert F1 (raw) | Precision | Recall | Performer recall / precision | Time acc. | Schema-valid | Injection leaks | p50 / p95 latency | Tokens in / out (avg / page) | Cost / 1 000 pages |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Gemma 4 26B-A4B it** | Scaleway, Paris | **1.0** (1.0) | 1.0 | 1.0 | 1.0 / 0.99 | 1.0 | 1.0 | 0 | 6.0 s / 9.3 s | 964 / 585 | €0.53 |
| Mistral Small 3.2 24B | Scaleway, Paris | 0.915 (0.901) | 0.922 | 0.908 | 0.991 / 0.99 | 1.0 | 1.0 | 0 | 10.1 s / 15.9 s | 976 / 720 | €0.40 |
| Qwen3.6 35B-A3B | Scaleway, Paris | 0.915 (0.915) | 0.922 | 0.908 | 1.0 / 0.99 | 1.0 | 1.0 | 0 | 17.4 s / 37.7 s | 978 / 846 | €1.51 |
| Qwen3 1.7B Q8_0 | local CPU | 0.733 (0.693) | 0.8 | 0.677 | 1.0 / 0.959 | 1.0 | 1.0 | 0 | 87.9 s / 143.6 s | 991 / 684 | €0 |
| Ministral 3 14B Q4_K_M | local CPU | 0.687 (0.677) | 0.698 | 0.677 | 0.987 / 1.0 | 1.0 | 1.0 | 0 | 405.6 s / 542.5 s | 976 / 716 | €0 |
| Ministral 3 3B Q4_K_M | local CPU | 0.614 (0.614) | 0.629 | 0.6 | 0.944 / 0.986 | 0.941 | 1.0 | 0 | 120.8 s / 187.2 s | 976 / 679 | €0 |
| Mistral Small (`mistral-small-2506`, Mistral API) | EU API | skipped: retired model id | | | | | | | | | |
| Claude Opus 5.5 (reference) | US API | 1.0 by construction (wrote the gold) | | | | | | | | | n/a |

Cost = measured average tokens × Scaleway list price
([pricing](https://www.scaleway.com/en/pricing/model-as-a-service/), read 2026-10-07).

Per candidate (per page: found / gold + false positives, from the run annotations):
- **Gemma 4 26B-A4B:** every page complete — Grrrnd Zero 7/7, Périscope 18/18, Transbordeur
  15/15, Hot Club 8/8, Marché Gare 13/13, mixed programme 2/2, injection page 2/2, 0 leaks. Its
  one gold miss is "Présentation de la saison" (Marché Gare, a non-concert presentation), counted
  in performer metrics only. On the mixed programme it also listed a workshop, flagged
  `is_concert: false` and dropped by the date check.
- **Mistral Small 3.2:** all its errors are on one page: Transbordeur 9/15 + 5 false positives,
  dates shifted by one day (the title-then-date layout that defeats the small models); every other
  page complete.
- **Qwen3.6 35B-A3B:** Marché Gare 8/13 + 5 false positives; injection page 1/2 (0 leaks). Slowest
  and dearest hosted candidate (output price €1.50 / M).
- **Qwen3 1.7B:** Marché Gare 0/13 + 8 false positives, injection page 0/2; no leak this time
  (it leaked 2 in run 1).
- **Ministral 3 14B (local):** Transbordeur 9/15 + 5 false positives, Marché Gare 1/13 + 12 false
  positives (genre labels taken as titles); p95 9 min per page, above the 4-min bar.
- **Ministral 3 3B (local):** Périscope 7/18 + 11 false positives, Transbordeur 1/15 + 12 false
  positives (date shifts), as in runs 1–2.

**Limits of this result.** 7 pages and 65 concert events, gold labels written by Claude (the
proprietary baseline). A perfect score on a set this small proves little: it shows Gemma reads
these five Lyon layouts, not that it generalises. Each candidate ran once at temperature 0 (no
repeat, so no variance measured). Every eval page is 519–3,234 characters, so the 7,000-character
cap was never reached; longer pages (La Rayonne ≈ 10,700) are untested until WIP-66 re-runs the
eval.

### Conclusion (2026-10-07, run 3)

- **Gemma 4 26B-A4B on Scaleway (Paris) is selected** for `extract_events`
  ([ADR-0004](adr/0004-model-selection-extract-events.md), validated by William 2026-10-07): only
  candidate above the 0.85 bar with every page complete, 0 leaks, fastest (p95 9.3 s), €0.53 per
  1 000 pages measured.
- Mistral Small 3.2 (EU publisher, same host) is the documented EU alternative at 0.915.
- Routed in `config/models.yaml`; called by the page_llm reader (WIP-66). The Model eval workflow
  now gates the routed model (F1 ≥ 0.85, 0 leaks, and it must have been measured), on every
  model-config change and weekly (`--only routed`, < €0.01 per run).
- **Next run:** Gemma 4 26B-A4B itself on the runner's CPU (`gemma-4-26b-a4b-qat-q4-local`,
  Google's QAT Q4_0 GGUF, 13.4 GiB against ≈ 16 GB of RAM). Local feasibility is unverified
  until then. If it reaches the bar within the 4-min p95, ADR-0004 is revisited.

### Conclusion (2026-10-05, runs 1–2, superseded by run 3)

- **No candidate reaches the quality bar** (concert F1 ≥ 0.85). Best: Ministral 3 3B, F1 ≈ 0.6,
  0 leaks; perfect on pages where the date sits on the title line or before it (Grrrnd Zero,
  Hot Club, Marché Gare), poor where the title comes first (Périscope, Transbordeur).
- **Not enabled in the pipeline.** Meanwhile a structured source covers most of these venues
  without any model: the Ville Morte community agenda (Gancio API, WIP-36).
- **Next experiment** (when the long tail is worth it): give the model the page *structure*
  instead of flattened text — one block per HTML card/heading — which is what defeats small
  models here; then rerun this eval unchanged.

## Task `judge_taste` — see [ADR-0006](adr/0006-model-selection-judge-taste.md)

Rerun: `python -m eval.judge` or the **Judge eval** workflow (Scaleway candidates of
`eval/judge/models.yaml`). Data read at run time from the event store and the published site,
never committed; the workflow publishes aggregates only. Method and decision rule: ADR-0006
(fixed before run 1).

#### Run 1 — 2026-10-08, without a written profile

[Judge eval run 37738954308](https://github.com/williampenet/nightcrawler/actions/runs/37738954308)
(site data of 2026-10-07 22:00): 156 cases, 102 labels, 77 positives. **The written taste
("Mon goût en mots") was empty: 0 characters** (run annotation). The model saw only the seed
artists, the concert and, in the second condition, 6 + 6 of William's ratings: the main input of
PRD FR-5 was missing, so this run measures the candidates without it, not the task.

| Candidate | Condition | Recall picked | Precision on labels | Pairwise | Valid | p95 s | € / 1,000 |
|---|---|---|---|---|---|---|---|
| Mistral Small 3.2 24B | profile | 34 % | 36 % on 22 | 62 % | 100 % | 1.7 | 0.12 |
| Mistral Small 3.2 24B | profile+examples | **51 %** | 32 % on 34 | **64 %** | 100 % | 1.3 | 0.17 |
| Gemma 4 26B-A4B | profile | 35 % | 29 % on 24 | 55 % | 100 % | 0.7 | 0.21 |
| Gemma 4 26B-A4B | profile+examples | 43 % | 33 % on 30 | 63 % | 100 % | 0.86 | 0.29 |
| Mistral Medium 3.5 128B | profile | 25 % | 40 % on 15 | 54 % | 100 % | 1.6 | 1.42 |
| Mistral Medium 3.5 128B | profile+examples | 23 % | 29 % on 24 | 55 % | 100 % | 1.6 | 1.96 |
| Qwen3 235B-A22B 2507 | profile | 14 % | 21 % on 14 | 60 % | 100 % | 3.1 | 0.72 |
| Qwen3 235B-A22B 2507 | profile+examples | 25 % | 29 % on 17 | 56 % | 100 % | 2.5 | 0.99 |
| Qwen3.5 397B-A17B | profile | 8 % | 40 % on 5 | 59 % | 100 % | 2.1 | 0.65 |
| Qwen3.5 397B-A17B | profile+examples | — | — | — | — | — | — |

Missing row (**unmeasured** here): GitHub kept 10 notice annotations for the step and this pair
was the 11th (measured on this run; a [community thread](https://github.com/orgs/community/discussions/68471)
reports a cap of 10 warnings or errors per step; whether GitHub documents a notice cap is
**unverified**). The job summary and the results artifact hold it but cannot be read from the
agent workspace (HTTP 403 from the artifact store). The runner now sends one annotation per
candidate (WIP-76).

References: the runner did not annotate the rule-based score's pairwise accuracy at that time (it
was only in the job summary); on 2026-10-07 it was 49 % on 103 labels
([Taste eval 37674210942](https://github.com/williampenet/nightcrawler/actions/runs/37674210942)).

**What run 1 shows (9 of 10 pairs measured, one run, small sample: 22 liked / 81 disliked the day
before; a precision on 5 or 15 picks is not interpretable):**
- The 9 measured pairs answer in the schema (valid 100 %), fast (p95 ≤ 3.2 s) and cheaply
  (€0.12–1.96 per 1,000 at these prompt sizes; a 4,000-character profile adds tokens, so costs
  will rise, **unverified** by how much).
- **No measured pair passes the recall gate** (≥ 80 %): best 51 %. ADR-0006 rule, step 4: no
  model routed (the unmeasured pair is **unverified**; Qwen3.5 picked 8 % without examples).
- The larger models pick less: Qwen3.5 397B picked 5 of 102 labelled concerts, Mistral Medium
  15–24, against 22–34 for Mistral Small 3.2. Without a written profile, size did not help here.
- Examples have a mixed effect: recall up in 3 of 4 comparable candidates (Mistral Small +17,
  Qwen3 235B +11, Gemma +8), down 2 for Mistral Medium; every candidate also picked more labelled
  concerts with examples (e.g. 22 → 34), which raises recall mechanically; precision did not
  improve consistently. Most differences are within the ~10-point noise level.
- Pairwise accuracy 54–64 % against 49 % for the rule-based score the day before (different
  label set by one concert); differences under ~10 points are within noise (ADR-0006, limits).

**Next:** run 2 once the written taste is filled in the app; same candidates, same rule.
