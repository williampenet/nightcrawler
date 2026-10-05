# Nightcrawler

> Nightcrawler finds the concerts near you that match your taste — including artists you don't know yet — and tells you before they sell out.

**Live demo:** {{DEMO_URL}} · **Backlog:** [Linear – WiP]({{LINEAR_PROJECT_URL}})

---

## What it does

- Discovers concert venues in your area automatically, including small independent ones, and collects their agendas daily.
- Scores every concert against your listening history, with a human-readable reason ("sounds like X").
- Weekly email digest, plus instant Telegram alerts for the concerts you can't miss.
- Listen to an extract, open the official ticket page, or share on WhatsApp in one tap.
- Learns from your "relevant / not for me" feedback.

## How it was built

This project is part of **WiP – Vibe coding**, a series of products built by AI agents and steered by a product manager.

| Role | Who | Responsibilities |
|---|---|---|
| Product Manager | William Penet (human) | Problem framing, PRD, design and architecture sign-off, demo acceptance |
| Senior engineer & orchestrator | Claude (AI) | Stack choice, ADRs, backlog breakdown, agent orchestration |
| Dev / QA / Review agents | Claude sub-agents | One PR per Linear ticket, tests, code review |

**Pipeline** (✋ = human validation)

1. PRD ✋ → [`docs/PRD.md`](docs/PRD.md)
2. Design system + key mockups (Claude Design) ✋
3. Architecture, ADRs, monthly cost estimate ✋ → [`docs/adr/`](docs/adr/)
4. Backlog in Linear
5. Autonomous build: one PR per ticket, green CI required
6. Continuous deployment of `main`
7. Demo + narrative ✋

**Model choice** (if the product uses an LLM): {{chosen model, publisher, licence, hosting}} — selected against {{n}} candidates and a proprietary baseline. Quality {{x}} vs baseline {{y}}, cost {{z}}× lower. Details: [ADR]({{docs/adr/...}}) · [evaluation](docs/MODEL_EVAL.md).
The build agents are Claude (Anthropic); the sovereignty / open-weights policy applies to the model running inside the product.

**Human vs agent split:** see [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md) for the dated log of every human and agent action.

## Stack

{{Filled from the accepted ADRs.}}

## Run locally

```bash
{{commands}}
```

## Monthly cost

{{Estimate from ADR, target ≤ €20/month.}}
