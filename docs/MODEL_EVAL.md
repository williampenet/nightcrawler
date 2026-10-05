# Model evaluation

> Delete this file if the product does not use an LLM at runtime.
> Decision record: `docs/adr/{{NNNN}}-model-selection-{{slug}}.md`

## Task under test
{{One paragraph: input, expected output, what "correct" means.}}

## Test set
- Location: [`eval/cases.jsonl`](../eval/cases.jsonl) — versioned, hand-written or hand-checked
- Size: {{n}} cases ({{n}} nominal, {{n}} edge cases, {{n}} adversarial / prompt-injection)
- Contains **no real personal data**
- Scoring: {{exact match / schema validity / rubric scored by script}} — LLM-as-judge only if unavoidable, and never with the candidate model as its own judge

## Results

Run: `{{eval command}}` · date {{YYYY-MM-DD}} · commit {{sha}}

| Model (revision) | Hosting | Quality | Schema-valid outputs | p95 latency | Cost / 1 000 req | Δ quality vs baseline | Cost ratio vs baseline |
|---|---|---|---|---|---|---|---|
| {{chosen}} | | | | | | | |
| {{candidate 2}} | | | | | | | |
| {{candidate 3}} | | | | | | | |
| {{baseline}} | US API | | | | | — | 1× |

**Cost method:** {{price per token source and date, or CPU/host cost amortised}}.

One results table per task (`config/models.yaml` → `tasks`).

## Escalation experiment (only for tasks with a `fallback`)

| Setup | Quality | Escalation rate | p95 latency | Cost / 1 000 req |
|---|---|---|---|---|
| Primary only | | 0 % | | |
| Fallback only | | 100 % | | |
| Primary → fallback | | | | |

**Verdict:** keep / drop escalation for this task — {{reason}}. Rule of thumb: keep it only if it closes most of the quality gap to "fallback only" at a fraction of its cost.

## Failure analysis
Typical errors of the chosen model and how the product mitigates them (validation, fallback, UI).

## Re-running in CI
The `model-eval` CI job runs the eval when `eval/cases.jsonl` exists. It fails if quality drops below the threshold in the ADR. Hosted-API candidates need their key as a CI secret; the job skips them when the secret is absent.
