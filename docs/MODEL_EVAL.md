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
  Qwen3 235B +11, Gemma +8), down 2 for Mistral Medium; every comparable candidate also picked more labelled
  concerts with examples (e.g. 22 → 34), which raises recall mechanically; precision did not
  improve consistently. Most differences are within the ~10-point noise level.
- Pairwise accuracy 54–64 % against 49 % for the rule-based score the day before (different
  label set by one concert); differences under ~10 points are within noise (ADR-0006, limits).

#### Run 1b — 2026-10-08, same scoring code, annotation change only (WIP-76), still without a written profile

[Judge eval run 37740875208](https://github.com/williampenet/nightcrawler/actions/runs/37740875208)
(site data of 2026-10-08 08:38): 155 cases, 100 labels (23 liked, 77 disliked), 78 positives,
written taste 0 characters. All 10 pairs measured this time.

References on the same labels: **rule-based score pairwise 48 %**; **former watch (Claude): 6 of
the 13 rated concerts it had reported were liked (46 %)**. Base rate: 23 % of labels are liked.

| Candidate | Condition | Recall picked | Precision on labels | Pairwise | Valid | p95 s | € / 1,000 |
|---|---|---|---|---|---|---|---|
| Mistral Small 3.2 24B | profile | 36 % | 35 % on 26 | 61 % | 100 % | 1.7 | 0.12 |
| Mistral Small 3.2 24B | profile+examples | **49 %** | 32 % on 38 | **62 %** | 100 % | 1.6 | 0.17 |
| Gemma 4 26B-A4B | profile | 35 % | 27 % on 26 | 61 % | 100 % | 1.6 | 0.21 |
| Gemma 4 26B-A4B | profile+examples | 40 % | 27 % on 33 | 58 % | 100 % | 1.1 | 0.29 |
| Mistral Medium 3.5 128B | profile | 20 % | 33 % on 15 | 51 % | 100 % | 1.5 | 1.43 |
| Mistral Medium 3.5 128B | profile+examples | 24 % | 29 % on 24 | 52 % | 100 % | 1.5 | 1.96 |
| Qwen3 235B-A22B 2507 | profile | 15 % | 18 % on 11 | 60 % | 100 % | 1.4 | 0.72 |
| Qwen3 235B-A22B 2507 | profile+examples | 23 % | 29 % on 17 | 56 % | 100 % | 1.5 | 0.99 |
| Qwen3.5 397B-A17B | profile | 8 % | 50 % on 6 | 57 % | 100 % | 2.0 | 0.65 |
| Qwen3.5 397B-A17B | profile+examples | 18 % | 38 % on 8 | 53 % | 100 % | 2.4 | 0.87 |

Between runs 1 and 1b, recall and pairwise differ by up to 6 points for the same pair (Gemma,
profile only, pairwise 55 % → 61 %) and precision by up to 10 points on 5–6 picks (Qwen3.5,
profile only, 40 % → 50 %), possibly because the labels and published
concerts changed overnight (102 → 100 labels) and because temperature 0 does not guarantee
identical answers from a hosted API; neither cause is measured separately. Run 1b also measures
the pair missing from run 1 (Qwen3.5 397B with examples: recall 18 %), which changes nothing.
Same reading as run 1: every pair below the 80 % recall gate, nothing routed. The two ~25 B
models pick the most labelled concerts (26–38 against 6–24); pairwise accuracy is 51–62 % for all
pairs, within the ~10-point noise level, so no candidate ranks clearly best. The former watch's
46 % on William's ratings is the only Claude reference; it is on 13 concerts only and not
directly comparable with the models' precision, since its picks are the reference positives
(ADR-0006, Baseline).

#### Run 1c — 2026-10-08, after William reported filling the written taste (still empty in the store)

[Judge eval run 37745152264](https://github.com/williampenet/nightcrawler/actions/runs/37745152264),
started by the merge of #68 (WIP-77) after William said at 09:38 that he had filled "Mon goût en
mots". The event store still held **0 characters** (run annotation); the text most likely stayed in
his browser without syncing (**unverified**, being checked with him). Same site data as run 1b
(2026-10-08 08:38), same 100 labels (23 liked, 77 disliked), 78 positives; references unchanged
(rule-based pairwise 48 %, former watch 6/13).

| Candidate | Condition | Recall picked | Precision on labels | Pairwise | Valid | p95 s | € / 1,000 |
|---|---|---|---|---|---|---|---|
| Mistral Small 3.2 24B | profile | 33 % | 36 % on 25 | 63 % | 100 % | 1.1 | 0.12 |
| Mistral Small 3.2 24B | profile+examples | **49 %** | 34 % on 35 | **63 %** | 100 % | 1.3 | 0.17 |
| Gemma 4 26B-A4B | profile | 35 % | 26 % on 27 | 57 % | 100 % | 1.0 | 0.21 |
| Gemma 4 26B-A4B | profile+examples | 38 % | 27 % on 33 | 60 % | 100 % | 1.2 | 0.29 |
| Mistral Medium 3.5 128B | profile | 23 % | 40 % on 15 | 53 % | 100 % | 1.6 | 1.43 |
| Mistral Medium 3.5 128B | profile+examples | 27 % | 32 % on 22 | 54 % | 100 % | 1.7 | 1.96 |
| Qwen3 235B-A22B 2507 | profile | 18 % | 33 % on 12 | 60 % | 100 % | 3.5 | 0.72 |
| Qwen3 235B-A22B 2507 | profile+examples | 23 % | 35 % on 20 | 59 % | 100 % | 3.6 | 0.99 |
| Qwen3.5 397B-A17B | profile | 8 % | 60 % on 5 | 58 % | 100 % | 2.4 | 0.65 |
| Qwen3.5 397B-A17B | profile+examples | 13 % | 38 % on 8 | 54 % | 100 % | 3.9 | 0.87 |

Same inputs as run 1b: the differences between 1b and 1c (up to 5 points on recall, e.g. Qwen3.5
with examples 18 % → 13 %; up to 4 on pairwise, Gemma profile 61 % → 57 %; up to 15 on precision,
Qwen3 235B profile 18 % → 33 % on 11–12 picks) are a direct measure of run-to-run variation at temperature 0 on this hosted API.
Mistral Small 3.2 with examples is the best pair on recall in all three runs (49–51 %), still far
from the 80 % gate. Since WIP-78 the runner makes no model call while the written taste is empty.

#### Run 2a — 2026-10-08, partial written taste (82 characters)

[Judge eval run 37746419688](https://github.com/williampenet/nightcrawler/actions/runs/37746419688),
started by the merge of #69 at 09:55 while William was typing his text again (09:56 message): the
store held **82 characters** of it (run annotation), most likely a sync made mid-typing (the page
pushes 1.5 s after the last edit, `app.js`; **unverified** for this case). Same site data and
labels as runs 1b–1c (100 labels, 23 liked), references unchanged (48 %, watch 6/13).

| Candidate | Condition | Recall picked | Precision on labels | Pairwise | Valid | p95 s | € / 1,000 |
|---|---|---|---|---|---|---|---|
| Mistral Small 3.2 24B | profile | 36 % | **60 % on 15** | **76 %** | 100 % | 3.4 | 0.12 |
| Mistral Small 3.2 24B | profile+examples | **45 %** | 55 % on 22 | 71 % | 100 % | 1.6 | 0.18 |
| Gemma 4 26B-A4B | profile | 19 % | 50 % on 12 | 65 % | 100 % | 0.7 | 0.21 |
| Gemma 4 26B-A4B | profile+examples | 28 % | 35 % on 20 | 61 % | 100 % | 0.8 | 0.29 |
| Mistral Medium 3.5 128B | profile | 19 % | 40 % on 10 | 63 % | 100 % | 1.5 | 1.41 |
| Mistral Medium 3.5 128B | profile+examples | 24 % | 54 % on 13 | 59 % | 100 % | 1.8 | 1.95 |
| Qwen3 235B-A22B 2507 | profile | 17 % | 50 % on 8 | 65 % | 100 % | 2.7 | 0.73 |
| Qwen3 235B-A22B 2507 | profile+examples | 19 % | 43 % on 14 | 60 % | 100 % | 3.4 | 1.00 |
| Qwen3.5 397B-A17B | profile | 12 % | 43 % on 7 | 66 % | 100 % | 2.3 | 0.66 |
| Qwen3.5 397B-A17B | profile+examples | 20 % | 33 % on 12 | 60 % | 100 % | 4.6 | 0.87 |

With only 82 characters of written taste, Mistral Small 3.2 (profile only) moves from 35–36 %
precision and 61–63 % pairwise (runs 1–1c) to 60 % on 15 picks and 76 %. Pairwise is the solid
signal: +13 points, against at most 4 points between runs 1b and 1c on unchanged inputs. The
precision gain is not established: its Wilson 95 % interval on 15 picks (36–80 %) reaches down to
the earlier values. Every pair still
misses the 80 % recall gate. Not the decision run: the text was incomplete.

#### Run 2 — 2026-10-08, full written taste (2,034 characters): decision run

[Judge eval run 37753009539](https://github.com/williampenet/nightcrawler/actions/runs/37753009539),
started by William (manual dispatch, 10:54). Written taste 2,034 characters; same site data and
labels as runs 1b–2a (100 labels, 23 liked, 77 disliked; 78 positives); references: rule-based
pairwise 48 %, former watch 6/13.

| Candidate | Condition | Recall picked | Precision on labels | Pairwise | Valid | p95 s | € / 1,000 |
|---|---|---|---|---|---|---|---|
| Mistral Small 3.2 24B | profile | 53 % | 36 % on 36 | 67 % | 100 % | 2.3 | 0.20 |
| Mistral Small 3.2 24B | profile+examples | **76 %** | 36 % on 42 | **68 %** | 100 % | 2.4 | 0.25 |
| Gemma 4 26B-A4B | profile | 37 % | 35 % on 26 | 66 % | 100 % | 1.0 | 0.34 |
| Gemma 4 26B-A4B | profile+examples | 45 % | 33 % on 33 | 66 % | 100 % | 0.9 | 0.42 |
| Mistral Medium 3.5 128B | profile | 27 % | 44 % on 16 | 61 % | 100 % | 1.6 | 2.15 |
| Mistral Medium 3.5 128B | profile+examples | 35 % | 33 % on 24 | 61 % | 100 % | 1.8 | 2.67 |
| Qwen3 235B-A22B 2507 | profile | 23 % | 31 % on 16 | 59 % | 100 % | 4.1 | 1.15 |
| Qwen3 235B-A22B 2507 | profile+examples | 33 % | 41 % on 17 | 63 % | 100 % | 4.0 | 1.41 |
| Qwen3.5 397B-A17B | profile | 24 % | 31 % on 13 | 60 % | 100 % | 5.5 | 0.97 |
| Qwen3.5 397B-A17B | profile+examples | 31 % | 38 % on 16 | 59 % | 100 % | 3.2 | 1.18 |

**Applying the ADR-0006 rule:** step 1 gates: no pair reaches 80 % recall (best: Mistral Small 3.2
with examples, 76 %; its Wilson 95 % interval on 78 positives is about 65–84 %, so the gap is
within noise but the gate is not met). Valid 100 %, p95 ≤ 5.5 s and pairwise above 48 % hold for
every pair. Step 4: **nothing routed**; options reported to William (2026-10-08): one improvement
iteration (the venue's own event description in the input, 10 + 10 examples) on the two ~25 B
models, then either route Mistral Small 3.2 if it passes, or a PM decision on the 80 % gate.

What the written taste changed (runs 1b–1c → 2, same labels): Mistral Small 3.2 with examples
49 % → 76 % recall, far beyond the run-to-run variation measured on unchanged inputs (≤ 5
points), and 62–63 % → 68 % pairwise, only marginally beyond it (≤ 4 points, one pair of runs). Precision on William's labels stays 31–44 %
for every pair (base rate 23 %); the larger models still pick fewer concerts and are not better.

#### Run 3 — 2026-10-08, iteration 1 (listing description, 10 + 10 examples), two ~25 B models

[Judge eval run 37759022114](https://github.com/williampenet/nightcrawler/actions/runs/37759022114),
started by the merge of #72 (WIP-79). Written taste 2,034 characters; same site data and labels as
run 2 (100 labels, 23 liked; 78 positives); **69 of the 155 cases had the listing's own
description**; references unchanged (rule-based pairwise 48 %, former watch 6/13).

| Candidate | Condition | Recall picked | Precision on labels | Pairwise | Valid | p95 s | € / 1,000 |
|---|---|---|---|---|---|---|---|
| Mistral Small 3.2 24B | profile | 56 % | 38 % on 40 | 70 % | 100 % | 1.5 | 0.20 |
| Mistral Small 3.2 24B | profile+examples | **77 %** | 39 % on 44 | **75 %** | 100 % | 1.1 | 0.28 |
| Gemma 4 26B-A4B | profile | 42 % | 36 % on 33 | 70 % | 100 % | 0.6 | 0.34 |
| Gemma 4 26B-A4B | profile+examples | 50 % | 40 % on 38 | 73 % | 99 % (2 × HTTP 429) | 0.7 | 0.48 |

**Applying the ADR-0006 rule:** no pair reaches 80 % recall (best: Mistral Small 3.2 with
examples, 60 of 78 positives, Wilson 95 % 66–85 %); every other gate holds (valid ≥ 99 %, p95 ≤
1.5 s, pairwise above 48 %). Step 4: nothing routed; the 80 % gate (PRD FR-5 AC) goes to William,
with the recommendation to accept Mistral Small 3.2 with examples.

What iteration 1 changed for that pair (run 2 → 3, same labels and positives): recall 76 % → 77 %
(within the ≤ 5-point run-to-run variation), pairwise 68 % → 75 %, precision 36 % on 42 → 39 %
on 44 (within noise). The pairwise rise is larger than the variation seen once on unchanged
inputs (≤ 4 points, one pair of runs) but within a single run's own uncertainty: with 23 liked ×
77 disliked labels its standard error is about 6 points (Hanley–McNeil, computed), so about ±12
points at 95 %; run 2a also gave this model 76 %. A ranking gain is plausible but **unverified**,
and the two changes (descriptions, more examples) are confounded.

#### Run 4 — 2026-10-08, after William's new ratings (34 usable likes)

[Judge eval run 37845697361](https://github.com/williampenet/nightcrawler/actions/runs/37845697361),
started by the merge of #74 (WIP-80). Site data of 2026-10-08 13:23; **152 labels (34 liked, 118
disliked)**, 87 positives, 205 cases, 92 with the listing's description; references on these
labels: rule-based pairwise 52 %, former watch 7/15. William reported 51 likes given; the eval
counts only likes on upcoming concerts present in that site data (docs/TASTE_EVAL.md), and the
breakdown of the gap is not measured (**unverified**: the Taste eval's per-reason breakdown covers
dislikes only; for likes it counts only ratings on concerts no longer published).

| Candidate | Condition | Recall picked | Precision on labels | Pairwise | Valid | p95 s | € / 1,000 |
|---|---|---|---|---|---|---|---|
| Mistral Small 3.2 24B | profile | 55 % | 40 % on 48 | 70 % | 100 % | 0.7 | 0.20 |
| Mistral Small 3.2 24B | profile+examples | **72 %** | **44 % on 57** | **79 %** | 100 % | 0.7 | 0.28 |
| Gemma 4 26B-A4B | profile | 44 % | 37 % on 43 | 67 % | 100 % | 0.9 | 0.34 |
| Gemma 4 26B-A4B | profile+examples | 54 % | 43 % on 51 | 68 % | 99 % (2 × HTTP 429) | 1.1 | 0.47 |

For Mistral Small 3.2 with examples: recall 72 % (63 of 87, Wilson 95 % 62–81 %), below the 80 %
gate and inside the 66–85 % interval of run 3's 77 %; pairwise 79 % against 52 % for the
rule-based score on the same labels, a 27-point gap against a single-run standard error of about
5 points (Hanley–McNeil on 34 × 118, computed); precision 44 % (Wilson 95 % 32–57 % on 57 picks) against a 22 % base rate (34/152).
ADR-0006 step 4: nothing routed. Proposed to William: route it as the ranking judge with an
amended gate (pairwise above the rule-based score and recall ≥ 70 %), pending his decision. The
70 % recall threshold was chosen after seeing this run: it is 2 points under run 4's 72 %, inside
the run-to-run variation (up to 5 points), so a later run can fail it; the pairwise part rests on a
27-point gap against a standard error of about 5 points.

#### Run 5 — 2026-10-09, iteration 2 (nearest ratings, learning curve, cross-validated cut-off)

[Judge eval run 37848873088](https://github.com/williampenet/nightcrawler/actions/runs/37848873088),
started by the merge of #76 (WIP-81). Same data as run 4 (site data 2026-10-08 13:23; 152 labels,
34 liked, 118 disliked; 87 positives; rule-based pairwise 52 %). Mistral Small 3.2 only.

| Condition | Recall picked | Precision on labels | Pairwise | @80 % cut-off: precision (95 %) on picks | recall | cut-offs |
|---|---|---|---|---|---|---|
| profile+examples (random) | 70 % | 41 % on 58 | 76 % | 33 % (24–43 %) on 89 | 91 % | discovery ≥ 30 / discovery ≥ 30 |
| **nn** (all ratings) | 71 % | 44 % on 57 | 77 % | **36 % (26–47 %) on 81** | 90 % | discovery ≥ 30 / discovery ≥ 30 |
| nn@2/3#1 | 63 % | 41 % on 56 | 71 % | 27 % (19–36 %) on 100 | 83 % | no ≤ 20 / discovery ≥ 30 |
| nn@2/3#2 | 75 % | 46 % on 55 | 78 % | 36 % (27–47 %) on 80 | 91 % | discovery ≥ 30 / discovery ≥ 30 |
| nn@1/3#1 | 71 % | 42 % on 55 | 73 % | 30 % (22–40 %) on 90 | 79 % | no ≤ 10 / discovery ≥ 30 |
| nn@1/3#2 | 68 % | 44 % on 52 | 74 % | 30 % (21–39 %) on 95 | 82 % | no ≤ 10 / no ≤ 10 |

All pairs valid ≥ 99 % (2 × HTTP 429 in nn@1/3#1), p95 ≤ 1.3 s, €0.28 per 1,000. At the 90 %
cut-off, `nn` gives precision 31 % (23–41 %) on 100 picks, recall 93 %.

**Applying the iteration 2 rule (ADR-0006):**
- **Gate 1 fails:** in `nn`, precision at the cross-validated 80 % cut-off is 36 %, under 40 %
  (recall 90 %, transfer check ≥ 75 % holds). The score is coarse: the cut-off that reaches 80 %
  on one half lands at "discovery ≥ 30" and overshoots to 90 % recall on the other.
- **Gate 2 passes, narrowly:** precision at that cut-off rises with the share of ratings, 1/3
  mean 30 % ≤ 2/3 mean 31.5 % ≤ all 36 %, +6 points (threshold +5), inside the ±12-point noise
  stated in the ADR; the 2/3 mean rests on one low seed (nn@2/3#1, 27 %), so the middle of the
  curve is weak.
- Nearest ratings vs random ones (all ratings): precision at the cut-off 36 % vs 33 %, default
  picks 44 % vs 41 %, pairwise 77 % vs 76 %: within noise.

In plain words: to reach 80 % recall the judge must also keep its `discovery` verdicts, and then
about 2 of every 3 rated concerts it picks are not to William's taste (29 liked of 81).

Nothing routed. The rule prescribes the fine-tuning (LoRA) study after a gate 1 failure. Also put
to William (2026-10-09), beyond the rule: keep rating and re-measure, since gate 1 is missed by
4 points within its interval and gate 2 suggests ratings help.

#### Run 6 — 2026-10-09, first run of the accepted rule (ADR-0006, WIP-82)

[Judge eval run 37887866174](https://github.com/williampenet/nightcrawler/actions/runs/37887866174),
started by the merge of #78: the first measurement of the fixed rule (`must_see`, `for_you`, or
`discovery` with confidence ≥ 30) after it was chosen. Site data of 2026-10-08 23:44; same labels
as runs 4–5 (152, 34 liked; 87 positives). The rounding of Deezer fan counts (WIP-83) was merged
after this run; the next run carries it, so a change then may come from either the rounding or
run-to-run variation.

| Condition | Shown recall (95 %) | Rated concerts shown | Precision: À ne pas rater | Pour toi | Découvertes | Tout voir |
|---|---|---|---|---|---|---|
| profile+examples | 88 % (80–94 %) | 57 % | 60 % on 10 | 37 % on 49 | 14 % on 28 | 9 % on 65 |
| **nn** (routed) | **85 % (76–91 %)** | 52 % | 78 % on 9 | 38 % on 47 | 13 % on 23 | 8 % on 73 |
| nn@2/3#1 | 79 % (70–86 %) | 51 % | 73 % on 11 | 33 % on 45 | 14 % on 21 | 11 % on 75 |
| nn@2/3#2 | 86 % (77–92 %) | 50 % | 88 % on 8 | 36 % on 44 | 21 % on 24 | 8 % on 76 |
| nn@1/3#1 | 78 % (68–86 %) | 52 % | 55 % on 11 | 39 % on 44 | 12 % on 24 | 11 % on 73 |
| nn@1/3#2 | 79 % (70–86 %) | 46 % | 67 % on 9 | 42 % on 43 | 11 % on 18 | 10 % on 82 |

- **Gate passed** (workflow green): the routed `nn` shows 85 % of the positives, the bar itself;
  run 5 gave 90 % with the same cut-off, so the run-to-run variation is visible here.
- **Sections check, first point:** "À ne pas rater" 78 % on 9 vs "Pour toi" 38 % on 47; it does
  not count yet (fewer than 10 concerts in "À ne pas rater", ADR-0006).
- **Découvertes:** 13 % precision, close to "Tout voir" (8 %): the section brings recall, few hits,
  as accepted (recall first).
- Shown recall along the learning curve: 1/3 of the ratings 78–79 %, 2/3 79–86 %, all 85 %; one
  run, inside the noise.
- Valid 98–100 % (HTTP 429 rate limits: 1 to 5 per condition, retried by `llm.chat_json` then
  counted as unusable); p95 ≤ 1.3 s; €0.278–0.28 per 1,000 judgements.


### Runs after WIP-88 and WIP-90 (2026-10-09, routed `nn` only shown)

| Run | Change | Shown recall (95 %) | Shown | À ne pas rater | Pour toi | Valid | €/1,000 |
|---|---|---|---|---|---|---|---|
| [37913091813](https://github.com/williampenet/nightcrawler/actions/runs/37913091813) | known artists (WIP-88) | 87 % (79–93 %) | 54 % | 54 % on 13 | 43 % on 42 | 100 % | 0.28 |
| [37934651876](https://github.com/williampenet/nightcrawler/actions/runs/37934651876) | venue texts (WIP-91/92) + faithful reasons (WIP-90) | 86 % (77–92 %) | 55 % | 67 % on 3 | 42 % on 50 | 90 % | 0.295 |

- **Gate passed** on both runs (≥ 85 %).
- Run 37934651876 carries two changes at once: 124 of 196 cases now have the listing's description (88 before), and the prompt has the WIP-90 rule. The effects below cannot be split between them (**unverified**).
- **Rejected reasons:** 19 of 196 `nn` answers were `unfaithful` (10 %). They count as shown in « Pour toi », as on the page. Whether they were true or false rejections is **unverified**: the eval publishes counts only.
- **« À ne pas rater » shrank from 13 to 3 rated concerts.** The model gives fewer `must_see` since the change; the cause is not established (**unverified**). The sections check (ADR-0006) cannot conclude on 3 concerts.
- In production the same day ([Pipeline 37934651881](https://github.com/williampenet/nightcrawler/actions/runs/37934651881)), 377 concerts were re-judged with the new prompt and texts:
  - 31 reasons rejected (8 %); their old judgements were deleted;
  - 9 transport failures.
