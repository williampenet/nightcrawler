# ADR-0004: Model selection — agenda extraction (`extract_events`)

- **Status:** Accepted — Gemma 4 26B-A4B on Scaleway (2026-10-07). Supersedes "no model enabled
  for now" (2026-10-05).
- **Date:** 2026-10-05, amended and decided 2026-10-07 (WIP-63)
- **Deciders:** William (PM), Claude (engineer)
- **Evaluation:** [`docs/MODEL_EVAL.md`](../MODEL_EVAL.md)

## Need
- **Task:** text of a venue's agenda page (no JSON-LD / microdata / iCal) → list of dated events
  `{title, date, time, performers[], is_concert}`. Most Lyon venues that matter (Périscope,
  Transbordeur, Grrrnd Zero, Marché Gare, Hot Club…) publish their agenda only as HTML.
- **Volume:** ~30 pages per daily run, ~1 000 pages / month; ~1–2.5 k tokens in, ≤ 2 k out.
  Pages are cached by content hash, so only changed pages are sent.
- **Quality bar:** concert F1 ≥ 0.85 after the deterministic checks, 0 injected events.
- **Latency bar:** batch job — p95 ≤ 4 min per page on a 4-vCPU GitHub runner.
- **Languages:** French (some English).
- **Data sensitivity:** none. Public venue pages; no user data ever reaches the model.

## Candidates

Shortlist sources: [QuelLLM.fr catalogue](https://quelllm.fr/catalogue) (starting point: it also
lists Pleias-RAG 1B, CroissantLLM 1.3B, SmolLM2 1.7B, Helium 1 2B — set aside as weaker at
instruction-following / structured output than the two picked), official model cards on
Hugging Face for licence, revision and hashes.

| Model | Publisher (country) | Licence | Size | Hosting | Est. cost / 1 000 pages | Notes |
|---|---|---|---|---|---|---|
| Ministral 3 3B Instruct 2512, Q4_K_M GGUF | Mistral AI (FR) | Apache 2.0 | 3.4 B (+0.4 B vision, unused) | local CPU, llama.cpp | €0 (CI minutes) | publisher GGUF, card recommends T < 0.1 for structured output |
| Qwen3 1.7B, Q8_0 GGUF | Alibaba Qwen (CN) | Apache 2.0 | 1.7 B | local CPU, llama.cpp | €0 | publisher GGUF; thinking mode off |
| Mistral Small 3.2 (`mistral-small-2506`) | Mistral AI (FR) | Apache 2.0 weights, hosted API | 24 B | Mistral API (EU) | ~€0.5 | **retired**: deprecated, retirement date 2026-04-30 ([card](https://docs.mistral.ai/models/model-cards/mistral-small-3-2-25-06)); same weights evaluated on Scaleway below |
| Claude Opus 5.5 | Anthropic (US) | Proprietary | n/a | US API | ~€30–60 (Opus-class list prices, to be confirmed) | **Baseline / reference**: wrote the gold labels, so it is the yardstick, not a measured row; never called at runtime |

*Open weights ≠ open source ≠ European:* both local candidates are Apache 2.0 (licence text checked
on the model cards); only the Mistral ones are European.

### Amendment 2026-10-07 (WIP-63): stronger candidates (7 B – ~35 B)

The 2–3 B models stay at F1 ≈ 0.6, below the bar. Research (QuelLLM.fr shortlist, official
model cards, EU provider pages, all read 2026-10-07) picked four larger candidates; they are run
by the same eval, scorer and bar. Hosted ones use **Scaleway Generative APIs**: Paris data
centre, inputs not used for training, OpenAI-compatible with strict `json_schema` structured
outputs (all properties required, optional ones nullable — our schema already is), one-time free
tier of 1 M tokens per project
([FAQ](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/faq.mdx),
[data privacy](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/reference-content/data-privacy.mdx),
[structured outputs](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/how-to/use-structured-outputs.mdx)).
Prices: [Scaleway pricing](https://www.scaleway.com/en/pricing/model-as-a-service/). Cost
estimate = 2.5 M tokens in + 2 M out per 1 000 pages (worst case); the eval also reports the
cost from measured tokens.

| Model | Publisher (country) | Licence | Size | Hosting | Est. cost / 1 000 pages | Notes |
|---|---|---|---|---|---|---|
| Mistral Small 3.2 24B Instruct 2506 (`mistral-small-3.2-24b-instruct-2506`) | Mistral AI (FR) | Apache 2.0 ([card](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506)) | 24 B | Scaleway, Paris | €1.08 (€0.15 / €0.35 per M) | EU model, EU host; served in **fp8** |
| Gemma 4 26B A4B it (`gemma-4-26b-a4b-it`) | Google (US) | Apache 2.0 ([card](https://huggingface.co/google/gemma-4-26B-A4B-it)) | 25.2 B total / 3.8 B active (MoE) | Scaleway, Paris | €1.63 (€0.25 / €0.50) | non-EU, non-CN comparison point; `reasoning_effort: none`; served in **bf16** |
| Qwen3.6 35B-A3B (`qwen3.6-35b-a3b`) | Alibaba Qwen (CN) | Apache 2.0 ([card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)) | 35 B total / 3 B active (MoE) | Scaleway, Paris | €3.63 (€0.25 / €1.50) | thinking off with `reasoning_effort: none` (Scaleway does not accept `chat_template_kwargs`, [supported models](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/reference-content/supported-models.mdx)); catalogue lists both **bf16** and **fp8**, the precision served by the serverless endpoint is not stated (**unverified**) |
| Ministral 3 14B Instruct 2512, Q4_K_M GGUF | Mistral AI (FR) | Apache 2.0 ([card](https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512)) | 13.5 B (+0.4 B vision, unused) | local CPU, llama.cpp | €0 | publisher GGUF, revision `74fac473…` and SHA-256 pinned; estimated 25–44 min per eval run, so it cannot meet the 4-min p95 bar on CPU; if it wins, production would use the same model through Mistral's API, `ministral-14b-2512` at $0.20 / $0.20 per M (≈ $0.90 / 1 000 pages, [card](https://docs.mistral.ai/models/model-cards/ministral-3-14b-25-12)) — **caveat:** API access is not settled: this ADR recorded that a key needs a paid plan (William, 2026-10-05), the [pricing page](https://mistral.ai/pricing) now lists API credits on the Free plan, and Experiment-plan requests "may be used to train Mistral's models" ([help](https://help.mistral.ai/en/articles/455206-how-can-i-try-the-api-for-free-with-the-experiment-plan)); to confirm before relying on it (**unverified**) |

Serving precision of the hosted candidates: from the model names in Scaleway's
[supported models](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/reference-content/supported-models.mdx)
page (read 2026-10-07): `mistral/mistral-small-3.2-24b-instruct-2506:fp8`,
`google/gemma-4-26b-a4b-it:bf16`, `qwen/qwen3.6-35b-a3b:bf16` and `:fp8`. The page covers both
serverless and dedicated deployments, so these are the catalogue precisions, not a measurement of
the serverless endpoint. Any effect of fp8 on quality is not assumed: the eval measures each model
as served.

Set aside (research notes): EuroLLM 9B (4 k context), Qwen3.8 27B (output price €2.7–3.3 / M),
Mistral Small 4 (119 B), local MoE models (Q4 files thought too large for the runner's 16 GB RAM;
revised after review: Gemma 4 26B-A4B QAT Q4_0 is 13.4 GiB and is now measured, see Decision), 8 B models
(cheap to add later via OVHcloud or Mistral if the 24 B results are close).

## Evaluation summary
See `docs/MODEL_EVAL.md` (7 pages, 72 labelled events of which 65 concerts are scored: 5 real
Lyon agendas captured 2026-10-05, 1 mixed theatre programme, 1 prompt-injection page).

Run 3 (Model eval [run 37591806394](https://github.com/williampenet/nightcrawler/actions/runs/37591806394),
2026-10-07), concert F1 after the deterministic checks:

| Candidate | F1 | Leaks | Schema-valid | p95 latency | Cost / 1 000 pages (measured tokens) |
|---|---|---|---|---|---|
| Gemma 4 26B-A4B (Scaleway) | **1.0** | 0 | 1.0 | 9.3 s | €0.53 |
| Mistral Small 3.2 24B (Scaleway) | 0.915 | 0 | 1.0 | 15.9 s | €0.40 |
| Qwen3.6 35B-A3B (Scaleway) | 0.915 | 0 | 1.0 | 37.7 s | €1.51 |
| Qwen3 1.7B Q8_0 (local) | 0.733 | 0 | 1.0 | 143.6 s | €0 |
| Ministral 3 14B Q4_K_M (local) | 0.687 | 0 | 1.0 | 542.5 s | €0 |
| Ministral 3 3B Q4_K_M (local) | 0.614 | 0 | 1.0 | 187.2 s | €0 |

**Limits:** 7 pages, 65 concert events, gold written by Claude (the baseline), one run per
candidate. A perfect score on a set this small proves little; it is a pass on these layouts, not a
measure of generalisation. All eval pages are ≤ 3,234 characters (the input cap was not reached).

## Decision
**Gemma 4 26B-A4B it (`gemma-4-26b-a4b-it`) on Scaleway Generative APIs (Paris)** for
`extract_events`, `reasoning_effort: none`, temperature 0. Validated by William on 2026-10-07
(chat, 11:47). Routed in `config/models.yaml`; called by the page_llm reader (WIP-66).

**Scaleway for now.** The same model run locally (Google's QAT Q4_0 GGUF, candidate
`gemma-4-26b-a4b-qat-q4-local`) is being measured by the next Model eval run. Revisit this
decision if it reaches the quality bar (F1 ≥ 0.85, 0 leaks) with acceptable latency (the 4-min p95
bar above): it would cost €0 and send nothing outside the runner.

Rationale (run 3 above):
- **Only candidate with every page complete:** F1 1.0, schema-valid on 7/7 pages, 0 injection
  leaks. The local candidates measured so far (Qwen3 1.7B, Ministral 3 3B and 14B) stay at
  0.61–0.73, below the 0.85 bar.
- **Fastest and cheap:** p95 9.3 s per page (bar: 4 min); €0.53 per 1 000 pages from measured
  tokens (964 in / 585 out per page at €0.25 / €0.50 per M,
  [pricing](https://www.scaleway.com/en/pricing/model-as-a-service/)), well inside the ~€20 / month
  budget at ~1 000 pages / month.
- **Licence and host:** Apache 2.0 ([card](https://huggingface.co/google/gemma-4-26B-A4B-it));
  publisher Google (US), but inference runs in Paris at an EU provider and only public page text
  is sent (Security below). Open weights, so the same model can move to another EU host or a
  dedicated deployment by editing config.
- **Size:** 3.8 B active parameters (MoE, 25.2 B total). Three hosted models pass the bar; among
  them Gemma has the best F1 and the lowest latency. Qwen3.6 has fewer active parameters (3 B of
  35 B) but scored 0.915, was 4× slower at p95 and costs 2.8× more per page; Mistral Small 3.2 is
  dense 24 B and cheaper by €0.13 / 1 000 pages.

**Documented EU alternative: Mistral Small 3.2 24B** (Mistral AI, FR, Apache 2.0, same Scaleway
endpoint, €0.40 / 1 000 pages): F1 0.915, above the bar, 0 leaks. All its errors are on one page
(Transbordeur, 9/15 + 5 false positives, dates shifted by one day). Switching is a config edit
(`model: mistral-small-3.2-24b-instruct-2506`, no `extra`) followed by an eval re-run. Second
choice on measured F1 only; its errors concentrate on one venue layout, so a Transbordeur-like page
would be shown with wrong dates.

**Escalation: none.** CLAUDE.md allows a fallback only when the primary's output fails schema
validation or a deterministic check and the eval shows the retry is worth its cost. Gemma's output
was schema-valid on 7/7 pages (validity 1.0), the case a fallback exists for, and its raw F1
equals its checked F1 (1.0): the checks removed none of its concerts (the run annotations note one
dropped event, the mixed-programme workshop). With every page complete there is no measured gain
for a second model to bring, only extra cost and latency. Reconsider if the CI eval or production logs
show invalid or mostly ungrounded answers.

**Input cap:** `limits.max_input_chars: 7000` (page text cut at a line boundary). Not raised here:
the eval pages are all ≤ 3,234 characters, and La Rayonne's agenda text is ≈ 10,700 characters
(measured by the orchestrator in Chrome, 2026-10-07). Instead of raising the cap, the page_llm
reader (WIP-66, PR #54) splits long pages into chunks of at most 3,200 characters
(`llm_chunk_chars`), so every call stays within the evaluated input size; no eval re-run needed.

**Alternatives considered for hosting (William asked, 2026-10-07):**
- **Run Gemma locally on the GitHub runner:** feasibility **unverified**, now measured. Google
  publishes a GGUF itself: `google/gemma-4-26B-A4B-it-qat-q4_0-gguf`, file
  `gemma-4-26B_q4_0-it.gguf`, 14,439,363,584 bytes (13.4 GiB), Apache 2.0, revision `d1c082be…`
  ([HF API](https://huggingface.co/api/models/google/gemma-4-26B-A4B-it-qat-q4_0-gguf?blobs=true),
  read 2026-10-07), so it meets the supply-chain rule below. The runner's "15 GB" of RAM (run
  37591806394 annotation) comes from `free -g`, which reports GiB: ≈ 16.1 GB, against a 13.4 GiB
  file. Memory is tight but not ruled out. An earlier version of this ADR called it impractical on
  a third-party 14.6 GB file and a GB/GiB mix-up (corrected after review). The file is an eval
  candidate (revision and SHA-256 pinned, kept out of the Actions cache); an out-of-memory error, a
  skip or a p95 above 4 min is a measurement too.
- **Fine-tune or use custom weights on Scaleway:** not possible on the serverless endpoint. Own
  models need a Dedicated Deployment ("supports both open-source models and your own uploaded
  proprietary models"), and fine-tuning itself "may need to use a separate training environment"
  ([FAQ](https://www.scaleway.com/en/docs/generative-apis/faq),
  [source](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/faq.mdx));
  dedicated deployments are billed hourly from €0.93 / h (L4-1-24G,
  [pricing](https://www.scaleway.com/en/pricing/model-as-a-service/)), ≈ €679 / month, far above
  budget. Not needed: Gemma already scores 1.0 on this set.
- Fine-tuning is kept as an option for a possible `judge_taste` task later (its own ADR).

**Supersedes** the 2026-10-05 decision ("do not enable LLM extraction yet", Ministral 3 3B routed
but not called), recorded in `docs/MODEL_EVAL.md` runs 1–2.

## Security & compliance
- **Data (re-checked 2026-10-07 for the decision):** the routed model receives only the public
  text of a venue's agenda page (scripts, comments and hidden templates removed, capped at
  `max_input_chars`), the date and the venue name: public page text, which may include incidental
  public contact details (a venue's or promoter's e-mail or phone printed on the page); EU
  processor, no training. No user data, taste profile, rating or identifier is ever part of the
  `extract_events` input. Local models send nothing anywhere. If the Mistral API is ever used, William first
  turns off training on the Mistral account (an account setting, not code).
- **Model supply chain:** local models from official publisher repos only; GGUF (no pickle);
  revision and SHA-256 pinned in config and verified after every download (`llm.ensure_weights`);
  llama.cpp from its official GitHub release, tag pinned. The routed model is hosted: no weights
  are downloaded; it is pinned by its Scaleway model id (`gemma-4-26b-a4b-it`, catalogued as
  `google/gemma-4-26b-a4b-it:bf16`). Scaleway does not publish a weight revision for serverless
  models, so a silent provider-side update cannot be excluded (**unverified**). It is caught
  within a week: the Model eval workflow runs every Monday on the routed model only
  (`--only routed`, < €0.01 per run: 7 pages × 964 in / 585 out tokens ≈ €0.004), as well as on
  every model-config change, and gates it. The gate fails the run when the candidate with the
  routed provider, model and `extra` is below F1 0.85, leaks an injected event, or was not
  measured at all (skipped, no key, error) (`min_quality` in `config/models.yaml`).
- **Prompt injection:** page text is wrapped as data (markers stripped from the page) with an
  explicit instruction; the request asks for strict `json_schema` structured output
  (`response_format`, OpenAI-compatible, on Scaleway and llama-server alike) and the answer is
  re-validated against the schema in code (`llm.validate`), never repaired. How the provider
  enforces the schema is not assumed. The only effect of the output is listing an event that links to the venue's
  own page — no other action. Resistance is *measured*, not guaranteed: the eval has an
  injection page and reports leaked events; the chosen model must leak 0 (CI gate).
- **Made-up events:** each event must pass deterministic checks or it is dropped — date in
  range, title found in the page with its day and month written within a few lines, performers
  written next to it. They remove invented events but not every misdating between neighbouring
  events (≈ 15 % of one-week shifts pass on the eval pages); date errors are counted by the eval.
- **Secrets:** `MISTRAL_API_KEY` and `SCW_GENAI_SECRET_KEY` (Scaleway IAM application limited to
  `GenerativeApisModelAccess`) in GitHub secrets only, passed to the eval step only today (WIP-66
  is to pass `SCW_GENAI_SECRET_KEY` to the Pipeline step that calls the reader); never logged (errors
  carry the model id and HTTP status only).
- **Scaleway prompt and retention policy** ([data privacy, docs source](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/reference-content/data-privacy.mdx),
  read 2026-10-07): "We do not collect, read, reuse, or analyze the content of your inputs,
  prompts, or outputs generated by the API"; "Your data is not used for training, retraining, or
  improving the base models"; aggregated and anonymised data kept up to 6 months; the full content
  of HTTP requests may be stored only in case of misuse harming the service, up to two weeks;
  region Paris, France. No account setting is needed to turn retention or training off. Even in
  the misuse case the stored content would be public page text.
- **Logs:** `llm.chat_json` logs and errors carry the model id and HTTP status only, never the
  prompt, the page text or the response body (unit test `test_http_error_never_echoes_body`).
- **Transparency (EU AI Act):** the label is built in WIP-66, together with the reader: PR #54
  adds a "Lu par IA" badge on concerts read by the model. No concert comes from the model before
  that. The page footer (`src/nightcrawler/web/index.html`) already says that some venues'
  agendas are read by an AI model (Gemma 4, Scaleway Paris) and labelled, and that the ranking is
  rule-based, without AI. It is correct once #54 lands and harmless before.

## Consequences
- Switching model = editing `config/models.yaml` and re-running `python -m eval` (e.g. to Mistral
  Small 3.2, the EU alternative above).
- The pipeline depends on an external API for HTML-only venues: when Scaleway is down or the key
  is missing, those pages are skipped for that run (`llm.ModelError`), structured sources are
  unaffected (`run_task` raises `ModelError` on transport failures; the skip is WIP-66's to build).
- The Model eval workflow fails when the routed model drops below the bar, leaks, or is not
  measured; a Scaleway outage or a missing key during that run also fails it, which is visible and
  re-runnable. The PR CI job runs the eval offline on the gold row only, so it never calls a model.
- Re-evaluate when a smaller EU model appears, when quality drops on new venues, or before
  raising `max_input_chars` (WIP-66).

## Cost impact
- **Routed model, measured:** €0.53 per 1 000 pages (run 3: 964 tokens in / 585 out per page at
  €0.25 / €0.50 per M). At ~1 000 pages / month: ≈ €0.53 / month; pages are cached by content
  hash, so unchanged pages are not re-sent. Longer pages (up to 7,000 chars) cost more per page;
  the €1.63 worst-case estimate above (2.5 k in / 2 k out) is the expected ceiling at this
  volume, but it is **unverified**: no measured page came near 7,000 characters and the page
  count per month is an estimate.
- **Eval:** one run of the three hosted candidates ≈ 7 × (1 549 + 1 696 + 1 824) ≈ 35 k tokens
  (run 3 averages), so the one-time 1 M free tokens cover ≈ 28 runs; beyond that, cents per run.
  Weekly routed-only run: ≈ 11 k tokens, < €0.01 (≈ €0.02 / month).
- Local candidates: €0 (public repo, free Actions minutes). The full Model eval job timeout goes
  from 150 to 240 min for the local Gemma candidate.
- Baseline at the same volume: roughly €30–60 / month — above the whole project budget.
- Rejected: a Scaleway dedicated deployment, from €0.93 / h ≈ €679 / month (Alternatives above).
