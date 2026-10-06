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
| 2026-10-05 | agent:dev | build | WIP-31 public Spotify Client ID; William confirms Spotify import and "Pour toi" ranking work live | |
| 2026-10-05 | agent:qa | inspect live site (Claude in Chrome) | 87/115 concerts from one aggregator page; key venues unreadable (no structured data) → coverage chantier WIP-32 | |
| 2026-10-05 | human:william | scope | Approves WIP-32: open-weight / local LLM use case for agenda extraction | |
| 2026-10-05 | agent:dev | build | WIP-33 provider abstraction (`llm.py`, `config/models.yaml`), `extract_events` task with deterministic checks, eval set (7 pages, 72 events), runner, Model eval workflow (llama.cpp on CPU), ADR-0004 draft | [ADR](adr/0004-model-selection-extract-events.md) |
| 2026-10-05 | agent:reviewer | review WIP-33 | REQUEST_CHANGES: 4 blocking (grounding accepted injected/misdated events, ADR overclaimed injection defence + no leak gate, unpinned mistral-small-latest, unbacked retention claim) + 12 non-blocking; blocking and most non-blocking fixed | |
| 2026-10-05 | human:william | budget | No paid Mistral plan: the hosted EU candidate stays documented but is not evaluated | |
| 2026-10-05 | agent:dev | build | WIP-35 venue attribution from event location (aggregator pages), "concerts" links ranked first in the probe, theatre/impro/humour/expo filter | |
| 2026-10-05 | agent:dev | build | WIP-36 Gancio source (Ville Morte agenda): places merged with OSM venues, tag-driven concert filter, capped detail fetches | [PR #22](https://github.com/williampenet/nightcrawler/pull/22) |
| 2026-10-05 | agent:reviewer | review WIP-35 | REQUEST_CHANGES: 4 blocking (room names became fake venues, longest match picked the wrong venue, substring match without word boundaries, genre words overrode atelier/conférence) + 6 non-blocking; fixed | PR #21 |
| 2026-10-05 | human:william | scope | Validates source order: ticketing platforms, Ticketmaster, aggregators, LLM last; asks to test Ville Morte | |
| 2026-10-05 | agent:qa | inspect Ville Morte (Claude in Chrome) | Gancio instance with a public JSON API: 218 events, 48 places incl. Grrrnd Zero, Périscope, Sonic, Marché Gare → WIP-36 | |
| 2026-10-05 | agent:reviewer | review WIP-36 | REQUEST_CHANGES: 2 blocking (malformed place crashed or collapsed Gancio venues; clash with WIP-35) + 7 non-blocking; fixed, rebased on WIP-35 | PR #22 |
| 2026-10-05 | agent:dev | eval | WIP-33 runs 1-2 on CPU: best Ministral 3 3B F1 ≈ 0.6 (bar 0.85), Qwen3 1.7B leaks injected events; LLM extraction not enabled (ADR-0004) | [MODEL_EVAL](MODEL_EVAL.md) |
| 2026-10-05 | agent:dev | infra | GitHub hosted runners repeatedly not acquired while the 40-min eval ran; eval moved off pull requests | |
| 2026-10-06 | agent:dev | fix | WIP-39 Gancio places without coordinates geocoded with the national address API (IGN Géoplateforme, BAN), capped and memoised | PR #23 |
| 2026-10-06 | agent:reviewer | review WIP-39 | REQUEST_CHANGES: 2 blocking (mis-geocode outside the zone silently dropped events; BAN/Etalab attribution missing) + 6 non-blocking; fixed | PR #23 |
| 2026-10-06 | agent:dev | feature | WIP-37 venues with no readable agenda: linked ticketing-platform pages read with the structured parsers (robots honoured, 2/venue, 40/run), per-platform counts in report.json and the CI annotation | PR #24 |
| 2026-10-06 | agent:reviewer | review WIP-37 | REQUEST_CHANGES: 3 blocking (userinfo host spoofing, login/checkout pages followed, out-of-zone platform events kept) + 8 non-blocking; fixed | PR #24 |
| 2026-10-06 | agent:dev | fix | WIP-26 Ticketmaster: `locale=*` (default `en` hides French-only events; 0 results for Lyon) | PR #25 |
| 2026-10-06 | agent:reviewer | review WIP-26 | APPROVE | PR #25 |
| 2026-10-06 | human:william | feedback | Recommendations off-topic: "Sheldon + Lupi'o + Asna" shown as "Proche de Acid Arab"; asks what "Pertinent" means | |
| 2026-10-06 | agent:dev | fix | WIP-40 related artists, tags and discovery badge only for confident identities (no homonyms, ≥ 1,000 fans, name ≥ 4 chars, exactly one MusicBrainz match); ADR-0002 amended | PR #26 |
| 2026-10-06 | agent:reviewer | review WIP-40 | REQUEST_CHANGES: 1 blocking (zero MusicBrainz match accepted as confident, ADR mismatch) + 7 non-blocking; fixed (unverified doubt, famous-artist exceptions) | PR #26 |
| 2026-10-06 | agent:dev | fix | WIP-42 cross-source de-dup: cleaned titles, same day ±90 min, same venue or ≤ 300 m, complete linkage, festival/genre words ignored; farther = `conflict`; merged `links` and id `aliases`; `report.dedup` | PR #27 |
| 2026-10-06 | agent:reviewer | review WIP-42 | REQUEST_CHANGES: 3 blocking (chained/unknown-time merges, short-title overlap, unstable ids) + 8 non-blocking; fixed | PR #27 |
| 2026-10-06 | human:william | scope | Wants feedback sent to the service (no export), duplicates fixed, asks for a central store with de-dup rules; asks for Scaleway pricing | |
| 2026-10-06 | agent:dev | adr | ADR-0005 proposed: Scaleway Serverless SQL (PostgreSQL, min 0 vCPU) + one function for feedback, ≈ €1–2/month | [ADR](adr/0005-event-store-and-feedback.md) |
| 2026-10-06 | human:william | validation | ADR-0005 accepted; William creates the Scaleway account | [ADR](adr/0005-event-store-and-feedback.md) |
| 2026-10-06 | agent:dev | build | WIP-41 (part 1) buttons "J'aime" / "Pas pour moi", "Mauvais rapprochement" link under guessed reasons (stops related/style guesses for that artist, stored locally until ADR-0005's store) | PR #29 |
| 2026-10-06 | agent:reviewer | review WIP-41 | REQUEST_CHANGES: keyboard focus lost after "Mauvais rapprochement" + 5 non-blocking; fixed | PR #29 |
| 2026-10-06 | human:william | setup | Scaleway account, project, API key and GitHub secrets created | |
| 2026-10-06 | agent:dev | build | WIP-44 step 1: Event store workflow provisions the Serverless SQL Database (REST API, idempotent) and applies schema migrations; CI gets a throwaway PostgreSQL for store tests | PR #30 |
| 2026-10-06 | agent:reviewer | review WIP-44 | REQUEST_CHANGES: guard the DROP SCHEMA test against non-local databases + 8 non-blocking (IAM rights, migration lock, GITHUB_ENV guard, naming, paths, index, tests); fixed | PR #30 |
| 2026-10-06 | agent:dev | build | WIP-44 step 3: feedback function (Scaleway Serverless Functions, python312: CORS, hashed bearer token, strict schema, parameterised insert), `deploy-feedback` command + Feedback function workflow, page queue (`feedback.js`) with send key in « Mes goûts » | PR #31 |
| 2026-10-06 | agent:reviewer | review WIP-44 | REQUEST_CHANGES: feedback queue loses items (remove by id); ADR rate limit dropped (429 restored) + 10 non-blocking; fixed | PR #31 |
| 2026-10-06 | agent:dev | fix | WIP-44 first real deploy: upload HTTP 403; upload headers de-duplicated, safer diagnostics | PR #32 |
| 2026-10-06 | agent:reviewer | review WIP-44 fix | APPROVE (root cause unconfirmed until the next deploy) + 4 non-blocking, 3 applied | PR #32 |
| 2026-10-06 | agent:dev | fix | WIP-44 upload fixed (duplicated content-type confirmed); Scaleway build then failed in its preparation phase; redeploy over a failed build | PR #33 |
| 2026-10-06 | agent:reviewer | review WIP-44 redeploy | APPROVE + 2 non-blocking | PR #33 |
| 2026-10-06 | human:william | setup | FEEDBACK_TOKEN secret created | |
| 2026-10-06 | agent:dev | deploy | Feedback function ready on Scaleway (python312, preflight 204, wrong token 401); URL published in config/app.yaml | |
| 2026-10-06 | agent:dev | fix | WIP-47: "J'aime" / "Pas pour moi" on every concert (concerts without an identified artist rated by id and performer names, sent with empty artist keys); dismissible error banner for page errors and Spotify connection news | PR #35 |
| 2026-10-06 | agent:reviewer | review WIP-47 | REQUEST_CHANGES: 2 blocking (liked concert overrides a disliked artist; like stuck after identification) + 8 non-blocking; fixed | PR #35 |
| 2026-10-06 | human:william | rule | Every claim and conclusion must be sourced (doc, measurement or real test); asks a real test of what Spotify still returns (PRD said "to confirm") | |
| 2026-10-06 | agent:dev | build | WIP-48 "Tester l'API Spotify": one-off OAuth with read-only scopes, calls 14 endpoints, shows statuses and field presence only (nothing stored); sourcing rule added to CLAUDE.md | PR #36 |
| 2026-10-06 | agent:reviewer | review WIP-48 | APPROVE + 4 non-blocking (links in comments, skipped rows reported, scope note, rule wording); applied | PR #36 |
| 2026-10-06 | agent:dev | build | WIP-46 part A: profile sync — `profile` table (migration 002), `GET/PUT /profile` on the feedback function (optimistic concurrency, 409 + merge), page sync (`profile.js`, debounced PUT, « Tout effacer » clears the server copy) | branch `wip-46-profile-sync` |

## Summary (filled at demo time)
- Tickets: {{n}} total, {{n}} done by agents, {{n}} by human
- PRs: {{n}} merged, {{n}} review rounds
- Human interventions: {{list}}
