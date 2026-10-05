# ADR-0001: POC architecture — batch pipeline in GitHub Actions, static page on GitHub Pages

- **Status:** Proposed (PM asked to build the POC first and review afterwards)
- **Date:** 2026-10-05
- **Deciders:** William (PM), Claude (engineer)

## Context
The POC (Linear WIP-5) must prove PRD FR-1 and FR-2 for one zone: find concert venues automatically, read their agendas, and show the upcoming concerts in a plain list. No user account, no taste profile, no LLM yet. Data changes once a day at most. Budget: ~€20/month, free tiers first.

Constraint found while building: the agent workspace cannot reach venue websites or public APIs (egress allowlist), so every real run happens in CI, and the code is tested against recorded fixtures.

## Options considered
| Option | Pros | Cons | Monthly cost |
|---|---|---|---|
| **A. Python batch in GitHub Actions → JSON + static page on GitHub Pages** | No server, no database, no secret beyond an optional API key; daily cron built in; easy to read for agents | No per-user state; page is public; data history not kept | €0 |
| B. Python worker + Postgres (Supabase, EU region) + web app | Ready for users, feedback and history | Two services to run before anything is proven; personal data appears earlier | €0 (free tier), more ops |
| C. Serverless functions scraping on request | Fresh data | Slow pages, many hits on venue sites, harder to be polite | €0–5 |

## Decision
Option A for the POC. The pipeline is one Python package (`src/nightcrawler`):

1. **Venues** — OpenStreetMap via the Overpass API (music venues, concert halls, clubs, arts centres, theatres, events venues, social / community centres, anything tagged `live_music=yes`) within the zone in `config/zone.yaml`; Ticketmaster Discovery API when `TICKETMASTER_API_KEY` is set. Venues from both are merged by distance (< 150 m) and name.
2. **Probe** — for each venue website: homepage, then up to 4 links that look like an agenda; read schema.org JSON-LD, then microdata, then iCal feeds. Ticketing widgets are recorded, not parsed.
3. **Concerts** — keep music events (schema.org type, ticketing category, music venue, or music keywords; the reason is stored), next 60 days, merged by venue + day + title.
4. **Output** — `site/data/{concerts,venues,report}.json` and a static page (vanilla JS) deployed to GitHub Pages. The run summary goes to the Actions job summary.

Option B becomes the target once the POC shows the coverage is good enough and per-user features (taste profile, feedback) start; that will be a new ADR.

## Security
- **Data:** no personal data is collected or processed in the POC (venues and public events only). Nothing is sent to an LLM.
- **Untrusted input:** all scraped content is data. Text is stripped of HTML and truncated; only `http(s)` URLs are kept; the page renders with `textContent` and checks every link's protocol; links open with `rel="noopener noreferrer nofollow"`.
- **Politeness / legal:** robots.txt honoured, bot user agent with a contact URL, one request per second per host, 20-hour cache shared between runs, 3 MB response cap (streamed, non-text responses not downloaded), robots.txt server errors treated as "disallow", redirects to another host re-checked against its robots.txt. OpenStreetMap data is attributed (ODbL) on the page.
- **Secrets:** `TICKETMASTER_API_KEY` only as a GitHub Actions secret; URLs carrying it are never cached to disk; httpx request logging is silenced so the key never reaches logs.
- **Supply chain:** dependencies pinned by major version in `pyproject.toml`; GitHub Actions from official `actions/*` publishers.
- **AI transparency:** N/A (no AI-generated content in the POC). The page states the list is collected automatically.

## Consequences
- Easier: zero infrastructure, everything reviewable in the repo, runs visible in the Actions tab.
- Harder: no history between runs (each run rebuilds the list); the page is public, so it must never show personal data — fine until per-user features arrive.
- To watch: venue sites without structured data (expected to be many; LLM fallback is WIP-15), Overpass availability, Actions run time (45 min cap), recurring iCal events (RRULE) not expanded yet.

## Cost impact
€0/month (GitHub Actions minutes on a public repo, GitHub Pages, Overpass and Ticketmaster free tiers). Total vs budget: €0 / €20.
