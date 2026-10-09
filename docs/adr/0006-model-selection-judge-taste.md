# ADR-0006: Model selection — taste judgement (`judge_taste`)

- **Status:** ✋ Accepted — Mistral Small 3.2 24B on Scaleway, nearest-rating examples, recall
  first (William, 2026-10-09 07:02 and 07:06, WIP-82). Proposed 2026-10-08 with the method and
  decision rules fixed before each run.
- **Date:** 2026-10-08
- **Deciders:** William (PM), Claude (engineer)
- **Evaluation:** [`docs/MODEL_EVAL.md`](../MODEL_EVAL.md) (section `judge_taste`), runner
  `python -m eval.judge`

## Need
- **Task (PRD v3 FR-5):** one concert (title, line-up, venue, date, and what `artists.json`
  knows: MusicBrainz tags, Deezer fans, Deezer related artists) + William's written taste
  ("Mon goût en mots", ≤ 4,000 chars) + his seed artists + optionally a few of his own ratings →
  `{verdict: must_see | for_you | discovery | no, reason (one French sentence), confidence 0-100}`,
  schema-validated (`src/nightcrawler/judge.py`).
- **Why a model:** the current rule-based score (`scoring.js`, artist similarity) orders William's
  ratings no better than chance: pairwise accuracy **49 %** over 1,782 liked/disliked pairs, 103
  labels ([Taste eval run 37674210942](https://github.com/williampenet/nightcrawler/actions/runs/37674210942),
  2026-10-07). His former LLM watch, judging against a written profile, gave picks he rates as good
  (PRD v3 §1).
- **Volume:** only new or changed concerts are judged (PRD FR-5, cache by content hash): ~500 at
  first (one window), then an estimated 20–50 per day, so ≤ ~1,500 per month (**unverified**,
  to measure once routed). Estimated ~2.5 k tokens in (profile, seeds, examples, concert) and
  < 100 out per judgement with reasoning off (**unverified**: the eval reports measured tokens).
- **Quality bar (PRD FR-5 AC):** recall of `must_see + for_you` ≥ 80 % on the positives, with the
  precision on William's labelled negatives reported. Decision rule below.
- **Latency bar:** batch job after the daily Pipeline; p95 ≤ 30 s per judgement.
- **Languages:** French prompt and reasons; concert text in French and English.
- **Data sensitivity: personal data.** The written profile, the seeds and the ratings describe
  William's tastes (PRD §7). They go only to an EU provider without retention or training, or to a
  local model. Concert text is public.

## Candidates

Shortlist sources (all read 2026-10-08): [QuelLLM.fr](https://quelllm.fr/catalogue) rankings for
[French](https://quelllm.fr/meilleur-llm/francais) (1. Magistral Small, 2. Mistral Small 3.2) and
[EU sovereign](https://quelllm.fr/meilleur-llm/souverain) models (all ≤ 47 B, none of the larger
models served today); Scaleway's
[supported models](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/reference-content/supported-models.mdx)
(validated 2026-08-14) and [prices](https://www.scaleway.com/en/pricing/model-as-a-service/);
official model cards for licences.

**Why larger models than for `extract_events`:** extraction reads facts printed on the page; this
task needs knowledge about the artists, often niche ones. LLM factual accuracy drops sharply from
popular to long-tail entities ([Head-to-Tail, Sun et al.](https://arxiv.org/pdf/2308.10168),
Table 4(a): GPT-4 40.3 % head, 33.4 % torso, 19.0 % tail), and text-profile music recommendation
is weaker for listeners of less popular artists
([Gagliano et al., MuRS 2025](https://ceur-ws.org/Vol-4045/paper3.pdf), read through a summary,
**unverified** in the text). Head-to-Tail also finds that size alone does not guarantee
knowledge (§3.4: LLaMA-33B slightly above LLaMA-65B), so the range 24 B → 397 B is measured
rather than assumed.

All five run on **Scaleway Generative APIs (Paris)**, the provider already used by ADR-0004:
zero data retention by default, inputs not used for training
([data privacy](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/reference-content/data-privacy.mdx)),
structured outputs (`json_schema`) and JSON mode supported by all its LLMs, with accuracy that "may vary between models"
([structured outputs](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/how-to/use-structured-outputs.mdx)).
Same key (`SCW_GENAI_SECRET_KEY`), no new account.

| Model (Scaleway id) | Publisher (country) | Licence | Size / active | Served as | € in / out per M | Est. € / 1 000 judgements* |
|---|---|---|---|---|---|---|
| `mistral-small-3.2-24b-instruct-2506` | Mistral AI (FR) | Apache 2.0 ([card](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506)) | 24 B dense | fp8 | 0.15 / 0.35 | 0.40 |
| `gemma-4-26b-a4b-it` | Google (US) | Apache 2.0 ([card](https://huggingface.co/google/gemma-4-26B-A4B-it)) | 25.2 B / 3.8 B | bf16 | 0.25 / 0.50 | 0.67 |
| `mistral-medium-3.5-128b` | Mistral AI (FR) | Modified MIT: commercial use allowed, exceptions for companies with large revenue ([card](https://huggingface.co/mistralai/Mistral-Medium-3.5-128B)); fine for this personal app, to re-check before any commercial use | 128 B dense | fp8 | 1.50 / 7.50 | 4.35 |
| `qwen3-235b-a22b-instruct-2507` | Alibaba Qwen (CN) | Apache 2.0 ([card](https://huggingface.co/Qwen/Qwen3-235B-A22B-Instruct-2507)) | 235 B / 22 B | not stated | 0.75 / 2.25 | 2.06 |
| `qwen3.5-397b-a17b` | Alibaba Qwen (CN) | Apache 2.0 ([card](https://huggingface.co/Qwen/Qwen3.5-397B-A17B)) | 397 B / 17 B | int4 | 0.60 / 3.60 | 1.79 |
| Claude (Anthropic) | Anthropic (US) | Proprietary | n/a | US API | — | **Baseline, not called** (below) |

\* 2.5 k tokens in + 80 out per judgement, list prices; **unverified** until the eval reports
measured tokens. Reasoning is turned off with `reasoning_effort: none` where Scaleway lists it
(Gemma 4, Mistral Medium 3.5, Qwen3.5), since thinking tokens are billed as output; Mistral
Small 3.2 and Qwen3 235B Instruct 2507 have no reasoning mode on that page.

*Open weights ≠ open source ≠ European:* four are Apache 2.0, Mistral Medium 3.5 is a custom
licence (open weights, not OSI open source); only the two Mistral models are European.

**Baseline.** Claude is **not called**: the prompt carries personal data, and PRD §7 forbids a
non-EU provider unless William approves it here (removing the name does not anonymise a taste
profile, GDPR Recital 26). Two references stand in for it:
1. **The former watch** (Claude judging the same kind of written profile): its precision on
   William's own ratings, i.e. among the rated concerts that the watch reported, the share he
   liked. Reported for context, not used by the decision rule: the overlap between rated concerts
   and the watch's in-window picks may be only a handful of concerts. The reference positives themselves are the watch's picks, so its recall on them is 100 %
   by construction and is not a comparison.
2. **The current rule-based score:** pairwise accuracy 49 % (above).

**Set aside:**
- *Local CPU* (hosting order 1): ADR-0004 measured Ministral 3 14B at p95 542 s per call on the
  4-vCPU runner (MODEL_EVAL run 3, extraction with long outputs). Judgements have short outputs
  but ~2.5 k-token prompts; ~340 calls would still take hours (**unverified** extrapolation), and the 2–3 B
  models that fit comfortably are expected to know even fewer niche artists (**unverified**
  for this task). Revisit if the 24–26 B
  candidates win, by measuring the local Gemma 4 QAT GGUF already pinned for ADR-0004.
- *gpt-oss-120b, DeepSeek V4 Flash, GLM 5.2* on Scaleway: listed with English (and Chinese) only
  on the supported-models page; the prompt and reasons are French.
- *Magistral Small* (QuelLLM's #1 for French): listed on Scaleway's supported-models page as not
  available serverless (dedicated only).
- *Llama 3.3 70B*: Llama 3.3 Community licence (custom, not OSI open source,
  [licence](https://huggingface.co/meta-llama/Llama-3.3-70B-Instruct/blob/main/LICENSE)), €0.90 /
  €0.90 per M on Scaleway (pricing page above), and older than the Qwen and
  Mistral models in the same price range.
- *Mistral Large 3, Qwen3.5 122B*: dedicated deployment only on Scaleway (from €0.93 / h,
  ADR-0004). *Mistral La Plateforme*: zero retention only on request on paid plans, the free plan
  may train on inputs ([help](https://help.mistral.ai/en/articles/347612-can-i-activate-zero-data-retention-zdr)).
- *Fine-tuning (LoRA)*: kept as an option if no candidate passes (PRD FR-5), with its own section.

## Evaluation method (fixed before results)
Runner `eval/judge` (Model eval for `judge_taste`), triggered in CI; publishes aggregates only
(public repo). Data, read at eval time and never committed:
- **William's labels:** liked / disliked concerts from the event store, built exactly as the taste
  eval does (`eval/taste/run.js` `buildLabels`, docs/TASTE_EVAL.md).
- **Reference positives:** the 68 rows of `eval/reference/watch_events.csv` (the watch's 69 rows
  minus one held at a private place, `eval/reference/README.md`). A row matched to a
  published concert (`report.json` coverage) is judged as that concert; other rows (past, or
  outside the window) are judged from the row's own text (artists, venue, date) and reported as a
  separate subset, since that text is cleaner than a real listing. A concert both rated and
  reported keeps William's label.
- **Two prompt conditions:** `profile` (written taste + seeds) and `profile+examples` (plus 6 liked
  and 6 disliked ratings, never the judged concert nor one sharing an artist with it:
  `judge.pick_examples`). The examples are William's labels, so the second condition is the one
  production would use only if it wins.

Metrics per candidate and condition: recall of `must_see + for_you` on all positives (reference ∪
liked) and with `discovery` counted; precision of `must_see + for_you` on William's labels
(Wilson 95 %); pairwise accuracy on his labels (same definition as the taste eval); schema-valid
rate; p50 / p95 latency; mean tokens; € / 1 000 judgements from measured tokens.

**Definitions.** *Positives* = William's liked concerts ∪ the reference positives (all three
subsets, including those judged from the row text). *Picked* = verdict `must_see` or `for_you`;
`discovery` is reported but never counts as picked. An answer that is unusable (transport or HTTP
error, timeout, invalid JSON, schema or `judge.check` failure) counts as `no` for recall and
precision and ranks below every valid answer for pairwise accuracy; pairwise on valid answers only
is reported too. *Precision* = liked / picked among William's labels; when nothing labelled is
picked it is 0.

**Decision rule** (one candidate × condition pair is chosen):
1. **Gates:** recall of picked on the positives ≥ 80 %; pairwise accuracy above the rule-based
   score's, measured on the same labels in the same run (49 % on 2026-10-07); valid answers
   ≥ 95 %; p95 latency ≤ 30 s.
2. Among the pairs that pass, let P be the best precision. The pairs within **10 points** of P
   (the noise level below) are equivalent on quality.
3. Among those, the cheapest per 1,000 judgements (measured tokens) wins; if an EU-publisher pair
   costs at most 1.2 × that one, the cheapest such EU pair wins instead.
4. If no pair passes the gates: no model is routed; report the gap and the options (more
   examples, the venue's own description in the input, a LoRA ADR section).

**Known limits:** 22 liked / 81 disliked labels and up to 68 reference positives: a Wilson interval on 22 items
is about ±20 points, so differences under ~10 points are noise. The positives were picked by
Claude from a written profile, so agreement with them partly measures agreement with Claude. One
run per candidate, temperature 0.

## Evaluation summary
**Run 1 (2026-10-08,
[Judge eval 37738954308](https://github.com/williampenet/nightcrawler/actions/runs/37738954308)):
not conclusive.** The written taste was empty (0 characters), so the models judged from seed
artists and examples only. 9 of 10 pairs were measured (the 10th annotation was cut by GitHub's
per-step cap: **unmeasured**): schema-valid on 100 % of answers, p95 ≤ 3.2 s, €0.12–1.96 per
1,000; recall of picked 8–51 %, all below the 80 % gate (best: Mistral Small 3.2 with examples,
51 %, pairwise 64 %). Rule step 4: nothing routed. Run 1b
([37740875208](https://github.com/williampenet/nightcrawler/actions/runs/37740875208), all 10
pairs, including the one run 1 missed, still no written taste) confirms it: recall 8–49 %; references on the same 100 labels:
rule-based score pairwise 48 %, former watch 6 of 13 rated picks liked (46 %). Run 1c
([37745152264](https://github.com/williampenet/nightcrawler/actions/runs/37745152264)) read the
written taste as empty again (not synced from William's device, **unverified**); same reading,
and it measures run-to-run variation on unchanged inputs (up to 5 points on recall, 4 on pairwise, 15 on precision with 11–12
picks). Since then the
runner makes no call while the written taste is empty. Details:
[`docs/MODEL_EVAL.md`](../MODEL_EVAL.md). Run 2a
([37746419688](https://github.com/williampenet/nightcrawler/actions/runs/37746419688)) read
82 characters of the written taste (most likely synced mid-typing, **unverified**): Mistral
Small 3.2 rose to 76 % pairwise (precision 60 % on 15 picks, not established), recall 36–45 %, still below the gate; not the decision run. **Run 2**
([37753009539](https://github.com/williampenet/nightcrawler/actions/runs/37753009539), full
written taste, 2,034 characters): best pair Mistral Small 3.2 with examples, recall 76 % (gate
80 %), precision 36 % on 42 picks, pairwise 68 % (rule-based 48 %), €0.25 per 1,000; every other
pair ≤ 45 % recall. Rule step 4: nothing routed; one improvement iteration proposed to William
before a PM decision on the gate.

### Iteration 1 (WIP-79, approved by William 2026-10-08 11:34, fixed before run 3)
Run 2 left Mistral Small 3.2 with examples 4 points under the recall gate. One iteration, same
rule, same labels and positives:
- **Input:** the listing's own event description (venue text), read from the event store
  (`raw_events.payload` through `concert_sources`, already stored by the pipeline as plain text),
  entities decoded, every run of angle brackets removed, capped at 600 characters, inside the CONCERT data block (`judge.description_text`). Never
  published: the site data has no description field.
- **Examples:** 10 liked + 10 disliked instead of 6 + 6, same leakage rules.
- **Candidates:** Mistral Small 3.2 and Gemma 4 26B-A4B only. The three larger models are
  retired: run 2 recall ≤ 35 % for each, at about 5–11 × the cost per 1,000 of Mistral Small 3.2 in
  the same condition (run 2 annotations).
- If Mistral Small 3.2 passes the gates, it is proposed for routing (✋ William); otherwise the
  80 % gate itself goes to William, since it comes from the PRD (FR-5 AC). A pass by a few points
  is inside the noise (run-to-run recall variation up to 5 points on unchanged inputs, runs
  1b–1c; Wilson 95 % on 78 positives about ±10 points) and will be reported as such.

**Run 3** ([37759022114](https://github.com/williampenet/nightcrawler/actions/runs/37759022114),
iteration 1, 69 of 155 cases with a description): Mistral Small 3.2 with examples recall 77 %
(60/78, Wilson 95 % 66–85 %), precision 39 % on 44 picks, pairwise 75 %, €0.28 per 1,000;
Gemma 4 with examples 50 % recall, pairwise 73 %. Still under the 80 % gate: the gate goes to
William (rule step 4).

**Run 4** ([37845697361](https://github.com/williampenet/nightcrawler/actions/runs/37845697361),
152 labels, 34 liked): Mistral Small 3.2 with examples recall 72 % (63/87, Wilson 62–81 %),
precision 44 % on 57 (base rate 22 %), pairwise 79 % (rule-based 52 % on the same labels);
Gemma 4 with examples 54 %, 43 %, 68 %. Still under the 80 % gate; an amended gate is proposed to
William (pairwise above the rule-based score, recall ≥ 70 %).

### Iteration 2 (WIP-81, fixed before run 5): William's condition
William (2026-10-08 23:27) agrees to route Mistral Small 3.2 **only if it has every chance of
reaching and passing 80 % recall (aiming at 90 %) as he keeps rating**. Measured so far, more
ratings did not raise recall (run 3, 23 likes: 77 %; run 4, 34 likes: 72 %), and the prompt sees
10 + 10 ratings drawn at random, so new ratings barely change what the model reads. Iteration 2
tests the condition before any routing:
- **Nearest ratings as examples** (`judge.pick_nearest`): the 10 liked and 10 disliked ratings
  most similar to the concert: related artists (Deezer, either way), shared styles (Jaccard on
  MusicBrainz tags), same venue; only confident identities bring artist data; the leakage rules
  of `pick_examples` are unchanged. Each new rating can then become the closest example of the
  concerts around it.
- **Learning curve:** the example pool is cut to 1/3 and 2/3 of William's ratings (per label,
  2 seeds each, nested) against all of them (condition `nn`); every case is still judged.
- **Cross-validated cut-off:** the judge's score (verdict, then confidence) is cut at the highest
  value reaching 80 % (and 90 %) recall on one half of the cases, chosen by a hash of their id;
  that cut-off decides the other half (cases tied at it are picked). Recall can always be raised
  by lowering the cut-off, which may then fall inside `discovery` or a low-confidence `no`: the
  out-of-fold recall lands near the target by construction (reviewer simulation on PR #76: mean
  80–83 % whatever the judge's quality). **The quality signal is therefore the precision at that
  cut-off**: how many of the concerts the judge picks to reach 80 % recall William actually likes.
  The run reports the out-of-fold recall and precision with their Wilson intervals and the answer
  each cut-off stands for (e.g. "discovery ≥ 70").
- **Candidate:** Mistral Small 3.2 only; Gemma 4 retired (runs 2–4 recall 45–54 % vs 72–77 %).
  `profile+examples` (random, iteration 1) is kept as the reference condition.

**Decision rule for iteration 2** (both needed, plus valid ≥ 95 % and p95 ≤ 30 s):
1. **80 % within reach at an acceptable precision:** in `nn`, at the cross-validated cut-off for
   80 % recall, out-of-fold precision on William's labels ≥ 40 % (base rate 22 % in run 4), with
   out-of-fold recall ≥ 75 % (a check that the cut-off transfers between halves, not a quality
   test).
2. **More ratings help:** that same precision rises with the share of ratings in the pool: the
   mean of the two `nn@1/3` conditions ≤ the mean of the two `nn@2/3` ≤ `nn`, and `nn` is at
   least 5 points above the `nn@1/3` mean. Pairwise accuracy along the same curve is reported.

If both hold, Mistral Small 3.2 with nearest examples is proposed for routing (✋ William), with
the cut-off it implies stated in plain words (which verdicts and confidences count as picked) and
a CI gate on the same cross-validated precision. If gate 1 holds but gate 2 does not, the curve is
measured again once William has added more ratings before any decision, since gate 2 sits close
to the noise; if gate 1 fails, nothing is routed and the next option is fine-tuning on William's
ratings (LoRA), with its own ADR section, method, data and GPU cost. The 90 % point is reported,
not gated. **Limits:** one run; with a third of the ratings the pool holds about 11 likes; a
precision measured on about 60 picks has a Wilson interval of roughly ±12 points, so gate 2 can
pass or fail by chance even though all conditions are measured on the same labels in the same
run.

**Run 5** ([37848873088](https://github.com/williampenet/nightcrawler/actions/runs/37848873088),
iteration 2): gate 1 fails (precision 36 %, 26–47 %, at the cross-validated 80 % cut-off, which
lands at "discovery ≥ 30" and gives 90 % recall; bar 40 %); gate 2 passes narrowly (precision 30 %
→ 31.5 % → 36 % with 1/3, 2/3 and all ratings). Nothing routed; next: more ratings and a
fine-tuning cost study (William to confirm).

## Decision
**Mistral Small 3.2 24B Instruct 2506 (`mistral-small-3.2-24b-instruct-2506`) on Scaleway
Generative APIs (Paris)**, temperature 0, with the written taste, the seeds, the listing's
description and the 10 + 10 of William's ratings nearest to the concert (`judge.pick_nearest`).
Routing: `config/models.yaml` → `tasks.judge_taste`.

**Recall first (William's decision, 2026-10-09 07:02):** "suggestions that turn out not to please
me are fine; what matters is that the app does not let pass a concert I am likely to like". The
iteration 2 gate on precision (≥ 40 %) is therefore dropped; precision is reported, not gated. A
concert is shown on the home page when the verdict is `must_see`, `for_you`, or `discovery` with
confidence ≥ 30 (`judge.section`, `DISCOVERY_MIN_CONFIDENCE`): the cut-off the run 5
cross-validation learnt for 80 % recall, which gave **90 % recall out of fold (82–94 %)**, about
half of William's rated concerts shown, and a precision of 36 % (26–47 %) against a 22 % base rate.
Everything else stays visible in "Tout voir".

Why this model (runs 2–5, `docs/MODEL_EVAL.md`): the best recall and pairwise accuracy of every
candidate in every run with the written taste (runs 4–5: pairwise 77–79 % against 52 % for the
rule-based score on the same labels), schema-valid on 100 % of answers, p95 ≤ 2.5 s, €0.20–0.28 per 1,000
judgements; EU publisher (Mistral AI, FR), Apache 2.0, EU host. The larger models picked fewer of
William's concerts at 5–11 × the cost; Gemma 4 26B-A4B stayed 18–31 points below in recall (runs 2–4, with examples). More
ratings improved precision at a fixed recall (run 5 learning curve: 30 → 31.5 → 36 %), narrowly.

**Sections check (William, 07:06):** "À ne pas rater" must be more precise than "Pour toi"; the
Judge eval reports the precision of each section, and if the difference does not hold the two
sections are merged.

**Gate:** the Judge eval fails when, in condition `nn`, the share of positives shown by
`judge.section` falls below `min_quality: 0.85` (aim 0.90), or when the routed model was not
measured. It runs on every change to the task, its runner or its candidates.

**Escalation: none.** Schema-valid on 100 % of answers in runs 2–5; nothing for a fallback to fix.

**Not chosen now:** fine-tuning on William's ratings (LoRA). Kept as the option if recall drops
under the gate or precision becomes a problem; it needs its own ADR section (hosting a fine-tuned
model is not offered serverless by Scaleway, ADR-0004).

## Security & compliance
- **Data:** sent per judgement: the written taste, up to 60 seed artist names, up to 20 of
  William's rated concerts (title, acts, venue; 12 before WIP-79) and the judged concert, with
  its listing's own description since WIP-79 (public venue text, not published by us). No identifier, no e-mail,
  no rating timestamps. Provider: Scaleway, Paris, zero retention by default, no training
  ([data privacy](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/reference-content/data-privacy.mdx));
  exceptions it lists: content of a request that triggers an HTTP 500 kept up to two weeks to fix
  it, and full request content stored "temporarily" when misuse harms the service.
- **Model supply chain:** hosted models, pinned by Scaleway id; Scaleway publishes no weight
  revision for serverless models (**unverified** silent updates), so the routed model gets a
  weekly re-run like ADR-0004.
- **Prompt injection:** concert text comes from venue pages, and so do the example concerts (titles,
  acts, venues): each goes in its own block (`<<<EXEMPLES`, `<<<CONCERT`), every run of two or more
  angle brackets is removed from the data so no field can rebuild a marker, and the system prompt
  says both blocks are data (`judge.SYSTEM`). Artist styles, fans and related artists are given
  only for confident identities (a doubtful match may be a homonym, `artists.py`). Output is
  strict `json_schema` and re-validated (`llm.validate` + `judge.check`); its only effect is the
  order and labels of concerts on William's own page.
- **Secrets:** `SCW_GENAI_SECRET_KEY` (model), `SCW_*` store secrets (read-only transaction), in
  GitHub secrets only.
- **Logs:** counts and rates only; never a title, a name, the profile, a prompt or a reason.
- **Transparency (EU AI Act):** reasons shown in the app are labelled as AI-generated (PRD FR-5).

## Consequences
- Switching model = editing `config/models.yaml` and re-running `python -m eval.judge`.
- Production must re-judge when the profile text changes, not only when the concert changes
  (cache key = concert hash + profile hash): to design in the follow-up ticket. Each profile edit
  re-judges the whole window (~500 concerts, ≈ €0.20–2.20 at the estimates above).

## Cost impact
- **Eval:** ~170 judgements × 2 conditions × 5 candidates ≈ 4.3 M tokens in, ≈ €3 one-off at list
  prices (**unverified**, measured by the run).
- Scaleway's free tier covers the first 1 M tokens of the project
  ([pricing](https://www.scaleway.com/en/pricing/model-as-a-service/)); how much of it ADR-0004's
  runs already used is not measured (**unverified**).
- **Production:** ≤ 1,500 judgements / month × €0.40–4.35 / 1 000 = €0.6–6.5 / month depending
  on the winner, inside the ~€20 budget.
