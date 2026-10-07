# Nightcrawler

> Nightcrawler finds the concerts near you that match your taste — including artists you don't know yet — and tells you before they sell out.

**Live demo:** https://williampenet.github.io/nightcrawler/ · **Backlog:** [Linear – WiP](https://linear.app/wip-coding/project/nightcrawler-bf075e17936e)

---

## What it does

- Discovers concert venues in your area automatically, including small independent ones, and collects their agendas daily. A concert listed by several sources (venue site, agenda, ticketing) is shown once, with all its links. (Concert ids moved to cleaned titles in WIP-42: links shared before that change and concerts hidden before it may not be recognised once.)
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

Python 3.12 batch pipeline run daily by GitHub Actions, static page on GitHub Pages ([ADR-0001](docs/adr/0001-poc-architecture.md)). Sources: OpenStreetMap (venues), venue websites (schema.org JSON-LD, microdata, iCal), the ticketing-platform pages they link to when their own site has no readable agenda (Shotgun, Dice, HelloAsso, Weezevent, Billetweb, Yurplan: same parsers, robots.txt honoured, at most 2 pages per venue and 40 per run, only events at the zone's known venues kept), Ticketmaster Discovery API (optional), Gancio community agendas listed in `config/zone.yaml` (e.g. [Ville Morte](https://agenda.villemorte.fr), Lyon); the PM's venues ("Mes salles") through readers configured in `config/zone.yaml` (a WordPress REST endpoint, or listing pages plus the schema.org Event JSON-LD of each event page); addresses without coordinates are geocoded with the IGN Géoplateforme (Base Adresse Nationale, Licence Ouverte 2.0).

## Run locally

```bash
pip install -e ".[dev]"
pytest -q                                   # offline tests
python -m nightcrawler run --out site       # real run, needs internet
python -m http.server -d site 8000          # then open http://localhost:8000
```

Spotify login (optional, personal use): create an app on the Spotify for Developers dashboard (owner needs Spotify Premium; Development Mode allows 5 users), register the redirect URI `https://williampenet.github.io/nightcrawler/`, and put its public Client ID in `config/app.yaml` (`spotify_client_id`). No secret is needed ([ADR-0003](docs/adr/0003-spotify-login-pkce.md)).

Optional secret: `TICKETMASTER_API_KEY` (GitHub Actions secret) adds Ticketmaster events.

Model eval (WIP-63): `SCW_GENAI_SECRET_KEY` (GitHub Actions secret) is the secret key of a Scaleway IAM application limited to the `GenerativeApisModelAccess` permission set (William, 2026-10-07; minimum set per [Scaleway docs](https://www.scaleway.com/en/docs/generative-apis/api-cli/using-generative-apis.md)). Only the **Model eval** workflow's eval step receives it, together with `SCW_DEFAULT_PROJECT_ID` (project-scoped URL `https://api.scaleway.ai/<project id>/v1`; without it the default project is used). Without the key, the Scaleway candidates are reported as "skipped: no key".

Event store (ADR-0005): `SCW_ACCESS_KEY`, `SCW_SECRET_KEY`, `SCW_DEFAULT_PROJECT_ID` (GitHub Actions secrets). The **Event store** workflow creates the Scaleway Serverless SQL Database (fr-par, 0–1 vCPU) once and applies `src/nightcrawler/store/sql/*.sql`; later steps get `DATABASE_URL` (masked), built at run time from the database endpoint, the API key owner's IAM id (or the optional `SCW_DB_USER` secret) and the secret key, so no URL secret is stored. The key's owner needs IAM read access to its own API key and Serverless SQL Database read/write rights. When these secrets exist, the nightly **Pipeline** also connects to the store (WIP-46, `run --store`; the URL stays inside the run step): it keeps every raw event, gives each concert the id of the stored concert it matches (same de-dup rules as within a run), so ids stay stable across runs, drops concerts marked `not_concert` in `overrides`, and publishes artists with a `wrong` feedback without related artists or tags (`doubt: "reported"`). If the store cannot be reached, the run continues without it (warning annotation with the error type only; `store=` in the run notice and `store` in `report.json`).

Feedback function (ADR-0005): add the GitHub secret `FEEDBACK_TOKEN` (a long random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`). The **Feedback function** workflow packages `functions/feedback/handler.py` with psycopg vendored in `package/` (Scaleway's Python convention), deploys it on Scaleway Serverless Functions (namespace `nightcrawler`, function `feedback`, python312, 0–1 instance, 128 MB) with `DATABASE_URL` and the token's SHA-256 as secret environment variables, and prints the function URL in a notice; without `FEEDBACK_TOKEN` it is skipped. Put that URL in `config/app.yaml` (`feedback_url`). On the page, open « Mes goûts » and paste the same token into « Clé d'envoi des avis »: it stays in that browser only. Ratings are queued in the browser (only when `feedback_url` is set) and sent when the function answers; they stay queued on errors and when the function's limit of 300 ratings per minute is reached (HTTP 429); « Clé refusée » means the key does not match. Profile sync (WIP-46): with the URL and the key set, the same function also keeps the taste profile (seed artists and their styles, ratings, hidden concerts; never the Spotify token) at `GET/PUT <feedback_url>/profile`, so it follows you on every device where the key is pasted. The page reads it on load and sends it 1.5 s after each change; « Profil synchronisé » / « Profil non synchronisé » shows the state (the browser copy is kept and sent again on the next change or visit); « Tout effacer » also empties the server copy. Without a key, nothing leaves the browser (ADR-0002 amendment). The deploy fails unless, with a wrong token, `GET <url>/profile` answers 401 and `GET <url>/` 405 (sub-path routing check). Known limitation: the function connects with the same API key as the pipeline (follow-up WIP-45: a dedicated IAM application with Serverless SQL rights only).

Taste eval (WIP-52): the **Taste eval** workflow runs after each successful **Pipeline** run and on demand, with the same `SCW_*` secrets (database looked up, never created; read-only transaction). It measures how `scoring.js` ranks your own ratings (leave-one-out, tiers sure / inferred / none, pairwise accuracy) and publishes aggregated counts and rates only, as one notice and the job summary; method and how to read the numbers: [`docs/TASTE_EVAL.md`](docs/TASTE_EVAL.md). Locally: `DATABASE_URL=… python -m eval.taste.load --site site` (needs Node.js). Known limitation: this job uses the pipeline's API key, which has broader rights than a read-only eval needs (the transaction itself is read-only); narrowing it is covered by follow-up WIP-45 (a dedicated IAM application with Serverless SQL rights only).

## Monthly cost

€0/month for the POC (see ADR-0001). Target ≤ €20/month.
