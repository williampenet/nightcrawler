# Build log

Dated journal of every meaningful action, human or agent. Feeds the "How it was built" story.

Actor values: `human:william`, `agent:orchestrator`, `agent:dev`, `agent:qa`, `agent:reviewer`, `agent:design`, `agent:ops`.

| Date | Actor | Stage | Action | Link |
|---|---|---|---|---|
| 2026-10-05 | human:william | brief | PM brief: cultural outings discovery, concerts first, minimal manual steps so it can open to other users | |
| 2026-10-05 | agent:orchestrator | discovery | Risky-assumption mapping and 90-day experiment plan (product-idea-tester framework); research on artist similarity methods and venue discovery | |
| 2026-10-05 | agent:orchestrator | PRD | PRD drafted from the brief and discovery, with challenge log (9 pushbacks) | [PRD](PRD.md) |
| 2026-10-05 | human:william | PRD | PRD validated; product named Nightcrawler; repository created from wip-template | |
| 2026-10-05 | human:william | POC | Scope set: venue detection, concert collection, plain list page, no ranking; no reference venue list or listening history given on purpose (generic approach) | |
| 2026-10-05 | agent:orchestrator | backlog | Linear project Nightcrawler, parent WIP-5 and tickets WIP-6 to WIP-15 | [Linear](https://linear.app/wip-coding/project/nightcrawler-bf075e17936e) |
| 2026-10-05 | agent:dev | build | POC code: OSM venue discovery, website probe (JSON-LD, microdata, iCal), concert filter, Ticketmaster source, static page, daily pipeline; 30 offline tests | |
| 2026-10-05 | agent:reviewer | review | REQUEST_CHANGES: 3 blocking (optional source could stop the run, partial dates completed with today, scraping job had deploy rights) + 10 non-blocking; all blocking and 8 non-blocking fixed | |
| 2026-10-05 | human:william | process | Global rule changed: maximum agent autonomy (commits, PRs, merges, deploys) to test automated vibe coding | |
| 2026-10-05 | human:william | setup | Ticketmaster API key added as a GitHub secret (WIP-13) | |
| 2026-10-05 | agent:orchestrator | build | PRs #1–#7 (WIP-6 to WIP-12) opened, CI green, merged | |
| 2026-10-05 | agent:orchestrator | ops | PR #8–#12: pipeline on push, annotations (raw logs unreadable from the agent session), Overpass 406 → Geofabrik extract + osmium, per-source counts | |
| 2026-10-05 | human:william | setup | GitHub Pages enabled; Ticketmaster key replaced twice (first one rejected, HTTP 401) | |
| 2026-10-05 | agent:orchestrator | POC | First published run: 462 venues, 200 with a website, 15 readable agendas, 114 concerts on 9 venues | [page](https://williampenet.github.io/nightcrawler/) |
| 2026-10-05 | human:william | scope | Next: sorting and personalisation | |
| 2026-10-05 | agent:orchestrator | architecture | ADR-0002: personalisation in the browser, pipeline enriches public data only (PR #13) | [ADR](adr/0002-client-side-personalisation.md) |
| 2026-10-05 | agent:reviewer | review WIP-24 | REQUEST_CHANGES: 1 blocking (malformed Deezer/MusicBrainz payloads could crash the run) + 6 non-blocking; all fixed | |
| 2026-10-05 | agent:dev | build | WIP-24 artist identification and enrichment (Deezer exact match, related artists, MusicBrainz tags), PR #14 | |
| 2026-10-05 | agent:reviewer | review WIP-25 | REQUEST_CHANGES: 5 blocking (stale filter state, unvalidated localStorage, MusicBrainz rate limit on double save, mobile layout, aria-live list) + 10 non-blocking; blocking and 8 non-blocking fixed | |
| 2026-10-05 | agent:dev | build | WIP-25 page: taste profile, "Pour moi" sort with reasons, filters, feedback, listen, share; JS unit tests run in CI | |
| 2026-10-05 | human:william | scope | Pivot: personal app for William only, with easy sharing (PRD v2) | [PRD](PRD.md) |
| 2026-10-05 | agent:dev | build | WIP-29 sharing: native share sheet, copy link, deep link to a concert (PR #17) | |
| 2026-10-05 | agent:reviewer | review WIP-30 | REQUEST_CHANGES: 3 blocking (ADR not amended for Spotify OAuth, callback/token handling untested, comma-splitting of imported names) + 6 non-blocking; all fixed | |
| 2026-10-05 | agent:dev | build | WIP-30 Spotify login (PKCE in the browser), ADR-0003 | [ADR](adr/0003-spotify-login-pkce.md) |

## Summary (filled at demo time)
- Tickets: {{n}} total, {{n}} done by agents, {{n}} by human
- PRs: {{n}} merged, {{n}} review rounds
- Human interventions: {{list}}
