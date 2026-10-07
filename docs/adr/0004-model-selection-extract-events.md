# ADR-0004: Model selection — agenda extraction (`extract_events`)

- **Status:** Accepted — no model enabled for now (2026-10-05); revisit pending eval of the
  WIP-63 candidates (2026-10-07)
- **Date:** 2026-10-05, amended 2026-10-07 (WIP-63)
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
| Mistral Small 3.2 24B Instruct 2506 (`mistral-small-3.2-24b-instruct-2506`) | Mistral AI (FR) | Apache 2.0 ([card](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506)) | 24 B | Scaleway, Paris | €1.08 (€0.15 / €0.35 per M) | EU model, EU host |
| Gemma 4 26B A4B it (`gemma-4-26b-a4b-it`) | Google (US) | Apache 2.0 ([card](https://huggingface.co/google/gemma-4-26B-A4B-it)) | 25.2 B total / 3.8 B active (MoE) | Scaleway, Paris | €1.63 (€0.25 / €0.50) | non-EU, non-CN comparison point; `reasoning_effort: none` |
| Qwen3.6 35B-A3B (`qwen3.6-35b-a3b`) | Alibaba Qwen (CN) | Apache 2.0 ([card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)) | 35 B total / 3 B active (MoE) | Scaleway, Paris | €3.63 (€0.25 / €1.50) | thinking off with `reasoning_effort: none` (Scaleway does not accept `chat_template_kwargs`, [supported models](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/reference-content/supported-models.mdx)) |
| Ministral 3 14B Instruct 2512, Q4_K_M GGUF | Mistral AI (FR) | Apache 2.0 ([card](https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512)) | 13.5 B (+0.4 B vision, unused) | local CPU, llama.cpp | €0 | publisher GGUF, revision `74fac473…` and SHA-256 pinned; estimated 25–44 min per eval run, so it cannot meet the 4-min p95 bar on CPU; if it wins, production would use the same weights through an EU API |

Set aside (research notes): EuroLLM 9B (4 k context), Qwen3.8 27B (output price €2.7–3.3 / M),
Mistral Small 4 (119 B), local MoE models (Q4 files do not fit the runner's 16 GB RAM), 8 B models
(cheap to add later via OVHcloud or Mistral if the 24 B results are close).

## Evaluation summary
See `docs/MODEL_EVAL.md` (7 pages, 72 events: 5 real Lyon agendas captured 2026-10-05,
1 mixed theatre programme, 1 prompt-injection page).

## Decision
**Do not enable LLM extraction yet.** Best candidate Ministral 3 3B Q4_K_M (local, Apache 2.0,
FR): concert F1 ≈ 0.6 with 0 injection leaks, below the 0.85 bar; Qwen3 1.7B is lower and
obeyed the injection page. Details in `docs/MODEL_EVAL.md` (runs 1–2).
`config/models.yaml` keeps Ministral as the routed model for the task so the harness, the eval
and a future pipeline integration (WIP-34) use one path; nothing in the pipeline calls it.
Coverage of these venues comes first from structured sources (Ville Morte / Gancio, WIP-36).
Revisit with structure-preserving input (one block per HTML card) — same eval, same bar.

**Escalation:** none for now. A hosted fallback (Mistral Small) would need a paid plan; it is reconsidered only if no local model meets the quality bar.

**WIP-63 candidates: pending run.** No result yet; the decision for the four candidates above is
written after the Model eval workflow has run with `SCW_GENAI_SECRET_KEY` set.

## Security & compliance
- **Data:** only public page text is sent; no personal data. Local models send nothing anywhere.
  If the EU API is ever enabled, William first turns off training on the Mistral account
  (an account setting, not code).
- **Model supply chain:** official publisher repos only; GGUF (no pickle); revision and SHA-256
  pinned in config and verified after every download (`llm.ensure_weights`); llama.cpp from its
  official GitHub release, tag pinned.
- **Prompt injection:** page text is wrapped as data (markers stripped from the page) with an
  explicit instruction; output is constrained by a JSON schema (grammar-constrained decoding)
  and re-validated. The only effect of the output is listing an event that links to the venue's
  own page — no other action. Resistance is *measured*, not guaranteed: the eval has an
  injection page and reports leaked events; the chosen model must leak 0 (CI gate).
- **Made-up events:** each event must pass deterministic checks or it is dropped — date in
  range, title found in the page with its day and month written within a few lines, performers
  written next to it. They remove invented events but not every misdating between neighbouring
  events (≈ 15 % of one-week shifts pass on the eval pages); date errors are counted by the eval.
- **Secrets:** `MISTRAL_API_KEY` and `SCW_GENAI_SECRET_KEY` (Scaleway IAM application limited to
  `GenerativeApisModelAccess`) in GitHub secrets only, passed to the eval step only; never
  logged (errors carry the model id and HTTP status only).
- **Scaleway (WIP-63):** public page text only, Paris; Scaleway states it does not reuse or
  train on inputs (data-privacy page above).
- **Transparency (EU AI Act):** concerts extracted by a model are labelled "extrait par IA" in
  the page (WIP-34).

## Consequences
- Switching model = editing `config/models.yaml` and re-running `python -m eval`.
- Re-evaluate when a smaller EU model appears or when quality drops on new venues.

## Cost impact
Local: €0 (public repo, free Actions minutes). A hosted EU fallback would be < €1 / month in tokens but needs a paid plan.
Baseline at the same volume: roughly €30–60 / month — above the whole project budget.
WIP-63 Scaleway candidates: €1.08–3.63 / month at ~1 000 pages (worst-case estimate above); one
eval run ≈ 7 pages × ~4.5 k tokens ≈ 32 k tokens per model, so the one-time 1 M free tokens cover
about ten runs of the three hosted candidates (research arithmetic, to be checked against the
measured tokens).
