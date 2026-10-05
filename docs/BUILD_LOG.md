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

## Summary (filled at demo time)
- Tickets: {{n}} total, {{n}} done by agents, {{n}} by human
- PRs: {{n}} merged, {{n}} review rounds
- Human interventions: {{list}}
