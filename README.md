# Nightcrawler

> Nightcrawler finds the concerts near you that match your taste — including artists you don't know yet — and tells you before they sell out.

**Live demo:** https://williampenet.github.io/nightcrawler/ · **Backlog:** [Linear – WiP](https://linear.app/wip-coding/project/nightcrawler-bf075e17936e)

---

## What it does

- Discovers concert venues in your area automatically, including small independent ones, and collects their agendas daily. A concert listed by several sources (venue site, agenda, ticketing) is shown once, with all its links. (Concert ids moved to cleaned titles in WIP-42: links shared before that change and concerts hidden before it may not be recognised once.)
- Scores every concert against your listening history, with a human-readable reason ("sounds like X").
- With a send key, the home page shows the judge's sections ("À ne pas rater", "Pour toi", "Découvertes", then "Tout voir"), each concert with its one-sentence reason labelled "IA"; concerts not judged yet stay in "Pour toi" (recall first). Without a key, the rule-based "Sûrs" / "À découvrir" tiers stay.
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

**Model choice:** agenda pages without structured data go through the `extract_events` task, routed to **Gemma 4 26B-A4B** (Apache 2.0) on Scaleway Generative APIs in Paris ([ADR-0004](docs/adr/0004-model-selection-extract-events.md), accepted 2026-10-07): concert F1 1.0 on the 7-page eval set (small set, gold written by Claude), 0 injection leaks, ≈ €0.53 per 1 000 pages measured, against 0.61–0.73 for the local 1.7–14 B candidates and 0.915 for Mistral Small 3.2, the EU alternative ([`docs/MODEL_EVAL.md`](docs/MODEL_EVAL.md)). Routed; called by the page_llm reader (WIP-66).
Taste judgements go through the `judge_taste` task, routed to **Mistral Small 3.2** on Scaleway Generative APIs in Paris ([ADR-0006](docs/adr/0006-model-selection-judge-taste.md)); they are stored privately and the page reads them with the send key (`GET /verdicts`, ADR-0007), rendering each reason as plain text with an "IA" label (WIP-86).
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

"Mes salles" without structured data (WIP-66): `priority_venues` entries with `reader.type: page_llm` (La Rayonne, Le Périscope, Chapelle de la Trinité, Les Subsistances) send their agenda page text to the `extract_events` task (`config/models.yaml`, ADR-0004) with `SCW_GENAI_SECRET_KEY`; without it they are reported as `skipped: no key`. The page text is split into chunks of at most `llm_chunk_chars` (3,200: the largest eval page is 3,234 characters, so every call stays within the evaluated input size), cut before a date line with 8 lines overlapping, at most `llm_chunks_per_page` (6) per page; one extraction per chunk. An invalid answer is counted by reason in the venue's status (`invalid answer: truncated|json|schema N`; transport failures are `error: ModelError (transport)`), and that chunk is split once in two before a date line near its middle (same overlap), each half (at most 75% of the chunk's characters, else no split) asked once within the same cap and cache; a split marker cached for the full chunk sends later runs straight to the halves (WIP-67). Answers are cached per chunk in `.cache/llm` (kept between Pipeline runs by `actions/cache`), keyed by the chunk text, venue, prompt version, models and sampling settings, so an unchanged chunk costs no tokens; at most `llm_calls_per_run` (40) calls per run reach the model. Outside music venues, a concert also needs the music-word rule, unless the entry sets `reader.trust_is_concert: true` (the agenda read is a concert programme; La Rayonne, Le Périscope, WIP-68), which only affects that reader's events, never the venue category. Concerts read this way carry `ai_extracted: true` and are labelled "Lu par IA" on the page (EU AI Act). Petit Bulletin is not read: its robots.txt opts out of AI crawlers. Each kept concert's own page (WIP-92) is found without a model, as the one link on the agenda's host whose text, `title` or `aria-label` names the concert, and read through the same fetcher (at most `reader.max_details`, default 40, per venue and 160 per run): its JSON-LD Event description, else its main text extracted by [trafilatura](https://pypi.org/project/trafilatura/) (Apache-2.0, pinned 2.2.0), becomes the concert's description (plain text, 500 characters); the venue's status counts `links`, `detail pages`, `with text` and `detail errors`.

Model eval (WIP-63): `SCW_GENAI_SECRET_KEY` (GitHub Actions secret; also the key of the routed `extract_events` model, which the Pipeline run step receives for the page_llm reader, WIP-66) is the secret key of a Scaleway IAM application limited to the `GenerativeApisModelAccess` permission set (William, 2026-10-07; minimum set per [Scaleway docs](https://www.scaleway.com/en/docs/generative-apis/api-cli/using-generative-apis.md)). Only the **Model eval** workflow's eval step receives it, together with `SCW_DEFAULT_PROJECT_ID` (project-scoped URL `https://api.scaleway.ai/<project id>/v1`; without it the default project is used). Without the key, the Scaleway candidates are reported as "skipped: no key". Merging a change to `eval/**` (or `config/models.yaml`, `llm.py`, `extract.py`) into `main` triggers the full Model eval run, about 2 h of CPU inference for the local candidates (run 3, 14B included; the local Gemma 26B-A4B candidate can add ~70 min, job timeout 240 min); every Monday a scheduled run checks the routed model only (`--only routed`, < €0.01) and fails if it is below the bar or not measured; for cheaper reruns use **Run workflow** (`workflow_dispatch`) with `only=` set to candidate ids, e.g. `mistral-small-3.2-scaleway,gemma-4-26b-a4b-scaleway,qwen3.6-35b-a3b-scaleway` (minutes, a few cents). Recommended: set a billing alert in the Scaleway console (e.g. €5), since the 1 M free tokens are one-time per project ([FAQ](https://raw.githubusercontent.com/scaleway/docs-content/main/pages/generative-apis/faq.mdx)).

Event store (ADR-0005): `SCW_ACCESS_KEY`, `SCW_SECRET_KEY`, `SCW_DEFAULT_PROJECT_ID` (GitHub Actions secrets). The **Event store** workflow creates the Scaleway Serverless SQL Database (fr-par, 0–1 vCPU) once and applies `src/nightcrawler/store/sql/*.sql`; later steps get `DATABASE_URL` (masked), built at run time from the database endpoint, the API key owner's IAM id (or the optional `SCW_DB_USER` secret) and the secret key, so no URL secret is stored. The key's owner needs IAM read access to its own API key and Serverless SQL Database read/write rights. When these secrets exist, the nightly **Pipeline** also connects to the store (WIP-46, `run --store`; the URL stays inside the run step): it keeps every raw event, gives each concert the id of the stored concert it matches (same de-dup rules as within a run), so ids stay stable across runs, drops concerts marked `not_concert` in `overrides`, and publishes artists with a `wrong` feedback without related artists or tags (`doubt: "reported"`). If the store cannot be reached, the run continues without it (warning annotation with the error type only; `store=` in the run notice and `store` in `report.json`).

Feedback function (ADR-0005): add the GitHub secret `FEEDBACK_TOKEN` (a long random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`). The **Feedback function** workflow packages `functions/feedback/handler.py` with psycopg vendored in `package/` (Scaleway's Python convention), deploys it on Scaleway Serverless Functions (namespace `nightcrawler`, function `feedback`, python312, 0–1 instance, 128 MB) with `DATABASE_URL` and the token's SHA-256 as secret environment variables, and prints the function URL in a notice; without `FEEDBACK_TOKEN` it is skipped. Put that URL in `config/app.yaml` (`feedback_url`). On the page, open « Mes goûts » and paste the same token into « Clé d'envoi des avis »: it stays in that browser only. Ratings are queued in the browser (only when `feedback_url` is set) and sent when the function answers; they stay queued on errors and when the function's limit of 300 ratings per minute is reached (HTTP 429); « Clé refusée » means the key does not match. Profile sync (WIP-46): with the URL and the key set, the same function also keeps the taste profile (seed artists and their styles, ratings, hidden concerts, and the written taste « Mon goût en mots » of at most 4,000 characters, WIP-73; never the Spotify token) at `GET/PUT <feedback_url>/profile`, so it follows you on every device where the key is pasted. The written taste is personal data: it lives in the browser and in the event store (Scaleway, EU), never in this repo or in logs; on a conflict between devices the most recent edit wins (last-writer-wins on the edit time), while the lists are merged as a union. The page reads it on load and sends it 1.5 s after each change; « Profil synchronisé » / « Profil non synchronisé » shows the state (the browser copy is kept and sent again on the next change or visit); « Tout effacer » also empties the server copy. Taste judgements (ADR-0007, WIP-85): `GET <feedback_url>/verdicts`, with the same key, returns the stored judgements of concerts that started less than 24 h ago or later (`{generated_at, verdicts: {concert id: {section, verdict, confidence, reason}}}`, `{}` when there are none), read in a read-only transaction; every answer of the function (all routes) is sent with `Cache-Control: no-store`; they are personal data, never published in `site/data` and never logged beyond a count. Without a key, nothing leaves the browser (ADR-0002 amendment). The deploy fails unless, with a wrong token, `GET <url>/profile` and `GET <url>/verdicts` answer 401 and `GET <url>/` 405 (sub-path routing check). Known limitation: the function connects with the same API key as the pipeline (follow-up WIP-45: a dedicated IAM application with Serverless SQL rights only).

Taste eval (WIP-52): the **Taste eval** workflow runs after each successful **Pipeline** run and on demand, with the same `SCW_*` secrets (database looked up, never created; read-only transaction). It measures how `scoring.js` ranks your own ratings (leave-one-out, tiers sure / inferred / none, pairwise accuracy) and publishes aggregated counts and rates only, as one notice and the job summary; method and how to read the numbers: [`docs/TASTE_EVAL.md`](docs/TASTE_EVAL.md). Locally: `DATABASE_URL=… python -m eval.taste.load --site site` (needs Node.js). Known limitation: this job uses the pipeline's API key, which has broader rights than a read-only eval needs (the transaction itself is read-only); narrowing it is covered by follow-up WIP-45 (a dedicated IAM application with Serverless SQL rights only).

## Monthly cost

€0/month for the POC (see ADR-0001). Target ≤ €20/month. Once the page_llm reader is on (WIP-66), model extraction adds ≈ €0.53 per 1 000 pages (measured, ADR-0004), about €0.5–1.6 / month at ~1 000 pages.
