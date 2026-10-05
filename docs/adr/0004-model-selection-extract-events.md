# ADR-0004: Model selection — agenda extraction (`extract_events`)

- **Status:** Proposed (decision filled from the first CI eval run)
- **Date:** 2026-10-05
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
| Mistral Small 3.2 (`mistral-small-2506`) | Mistral AI (FR) | Apache 2.0 weights, hosted API | 24 B | Mistral API (EU) | ~€0.5 | **not evaluated**: an API key needs a paid plan (William, 2026-10-05); kept as the fallback option |
| Claude Opus 5.5 | Anthropic (US) | Proprietary | n/a | US API | ~€30–60 (Opus-class list prices, to be confirmed) | **Baseline / reference**: wrote the gold labels, so it is the yardstick, not a measured row; never called at runtime |

*Open weights ≠ open source ≠ European:* both local candidates are Apache 2.0 (licence text checked
on the model cards); only the Mistral ones are European.

## Evaluation summary
See `docs/MODEL_EVAL.md` (7 pages, 72 events: 5 real Lyon agendas captured 2026-10-05,
1 mixed theatre programme, 1 prompt-injection page).

## Decision
Pending the CI run. Provisional routing: `config/models.yaml` → `tasks.extract_events` =
Ministral 3 3B Q4_K_M, local, revision `eb599d40…`, SHA-256 pinned.

**Escalation:** none for now. A hosted fallback (Mistral Small) would need a paid plan; it is reconsidered only if no local model meets the quality bar.

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
- **Secrets:** `MISTRAL_API_KEY` in GitHub secrets only.
- **Transparency (EU AI Act):** concerts extracted by a model are labelled "extrait par IA" in
  the page (WIP-34).

## Consequences
- Switching model = editing `config/models.yaml` and re-running `python -m eval`.
- Re-evaluate when a smaller EU model appears or when quality drops on new venues.

## Cost impact
Local: €0 (public repo, free Actions minutes). A hosted EU fallback would be < €1 / month in tokens but needs a paid plan.
Baseline at the same volume: roughly €30–60 / month — above the whole project budget.
