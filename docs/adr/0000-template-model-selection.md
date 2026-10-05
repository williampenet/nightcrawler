# ADR-NNNN: Model selection — {{capability, e.g. "event tagging"}}

- **Status:** Proposed | ✋ Accepted | Superseded by ADR-XXXX
- **Date:** {{YYYY-MM-DD}}
- **Deciders:** William (PM), Claude (engineer)
- **Evaluation:** [`docs/MODEL_EVAL.md`](../MODEL_EVAL.md)

> Required whenever the product calls an LLM at runtime. One ADR per task (one task = one model).
> Policy: prefer the smallest model that does the job, permissive licences, EU publishers and EU hosting.
> A large proprietary model is always included **as a baseline for comparison only**.

## Need
- **Task:** {{what the model does, input → output}}
- **Volume:** {{requests/month, expected tokens in/out per request}}
- **Quality bar:** {{metric and threshold, e.g. "≥ 90 % exact match on the eval set"}}
- **Latency bar:** {{p95 target}}
- **Languages:** {{e.g. French + English}}
- **Data sensitivity:** {{none / personal data / confidential}} → constrains hosting

## Candidates

Shortlist sources: [QuelLLM.fr catalogue](https://quelllm.fr/catalogue) (starting point only), official model cards on Hugging Face (licence and figures are verified there), {{other}}.

| Model | Publisher (country) | Licence | Size / active params | Hosting option | Est. cost / 1 000 req | Notes |
|---|---|---|---|---|---|---|
| {{candidate 1}} | | | | local / EU API / GPU | | |
| {{candidate 2}} | | | | | | |
| {{candidate 3}} | | | | | | |
| {{baseline, e.g. a frontier proprietary model}} | | Proprietary | n/a | US API | | **Baseline only** |

Reminders:
- *Open weights ≠ open source ≠ European.* Check the licence text itself (Apache 2.0 / MIT preferred; flag non-commercial, research-only or custom licences).
- Hosting preference: (1) local / CPU / in-browser, (2) EU-hosted inference API, (3) dedicated GPU — only with a cost justification below.

## Evaluation summary
Copy the headline table from `docs/MODEL_EVAL.md` (quality, p95 latency, cost / 1 000 req, vs baseline).

## Decision
{{Chosen model, exact version / revision, hosting.}} Why it beats the alternatives for this need.
Routing entry: `config/models.yaml` → `tasks.{{task}}`.

**Escalation:** none | fallback to {{model}} on validation failure — kept / dropped per the escalation experiment in `docs/MODEL_EVAL.md`.

## Security & compliance
- **Data:** what is sent to the model; personal data minimised; no personal data to a non-EU provider; provider retention and training opt-out confirmed ({{link to provider terms}}).
- **Model supply chain:** official source only; `safetensors` / GGUF from the publisher (no pickle); revision and SHA-256 pinned in config.
- **Prompt injection:** all external content is treated as data; output validated against a schema; no side-effecting action without a deterministic check.
- **Secrets:** API keys in CI / host secrets only — variable names: {{MODEL_API_KEY}}.
- **Transparency (EU AI Act):** how users are told AI is involved.

## Consequences
- Switching model = changing `{{config path}}` (provider abstraction) and re-running the eval.
- Re-evaluate when: {{a new candidate appears, quality drops, cost changes}}.

## Cost impact
Monthly cost at expected volume, vs the baseline and vs the ~€20/month budget.
