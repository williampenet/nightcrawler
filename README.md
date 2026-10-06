# Nightcrawler

> Nightcrawler finds the concerts near you that match your taste — including artists you don't know yet — and tells you before they sell out.

**Live demo:** https://williampenet.github.io/nightcrawler/ · **Backlog:** [Linear – WiP](https://linear.app/wip-coding/project/nightcrawler-bf075e17936e)

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

**Model choice:** no LLM in the POC. The planned LLM fallback for unstructured agenda pages (WIP-15) will get its own model-selection ADR.
The build agents are Claude (Anthropic); the sovereignty / open-weights policy applies to the model running inside the product.

**Human vs agent split:** see [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md) for the dated log of every human and agent action.

## Stack

Python 3.12 batch pipeline run daily by GitHub Actions, static page on GitHub Pages ([ADR-0001](docs/adr/0001-poc-architecture.md)). Sources: OpenStreetMap (venues), venue websites (schema.org JSON-LD, microdata, iCal), Ticketmaster Discovery API (optional), Gancio community agendas listed in `config/zone.yaml` (e.g. [Ville Morte](https://agenda.villemorte.fr), Lyon); addresses without coordinates are geocoded with the IGN Géoplateforme (Base Adresse Nationale, Licence Ouverte 2.0).

## Run locally

```bash
pip install -e ".[dev]"
pytest -q                                   # offline tests
python -m nightcrawler run --out site       # real run, needs internet
python -m http.server -d site 8000          # then open http://localhost:8000
```

Spotify login (optional, personal use): create an app on the Spotify for Developers dashboard (owner needs Spotify Premium; Development Mode allows 5 users), register the redirect URI `https://williampenet.github.io/nightcrawler/`, and put its public Client ID in `config/app.yaml` (`spotify_client_id`). No secret is needed ([ADR-0003](docs/adr/0003-spotify-login-pkce.md)).

Optional secret: `TICKETMASTER_API_KEY` (GitHub Actions secret) adds Ticketmaster events.

## Monthly cost

€0/month for the POC (see ADR-0001). Target ≤ €20/month.
