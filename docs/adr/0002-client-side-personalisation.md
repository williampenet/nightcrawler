# ADR-0002: Personalisation runs in the browser; the pipeline only enriches public data

- **Status:** Accepted (PM delegated architecture decisions to the agents on 2026-10-05; reviewable at demo)
- **Date:** 2026-10-05
- **Deciders:** William (PM), Claude (engineer)

## Context
WIP-22 adds sorting and personalisation (PRD FR-4, FR-5, FR-6, FR-8). Constraints:
- The PM deliberately gives no access to his listening history: the approach must work for anyone, from scratch.
- ADR-0001 keeps the product a static page on GitHub Pages, rebuilt daily, €0/month.
- A taste profile is personal data (GDPR); the page is public.
- Research for the experiment plan: Spotify closed related artists, recommendations and genres to new apps (2024–2026); LLMs are unreliable judges of musical similarity (ISMIR 2024). Co-listening graphs and style tags are the usable signals.

## Options considered
| Option | Pros | Cons | Monthly cost |
|---|---|---|---|
| **A. Pipeline enriches artists with public data; profile + scoring in the browser (localStorage)** | No account, no server, no personal data leaves the device; works for any visitor; instant re-ranking | Profile lost if browser storage is cleared; no cross-device sync; scoring limited to what the page ships | €0 |
| B. Accounts + backend (Supabase EU) storing profiles | Sync, notifications later | Auth, GDPR obligations, ops, before the value is proven | €0 (free tier) + ops |
| C. Profile in the URL (shareable link) | No storage | Leaks tastes in links and logs; long URLs | €0 |

## Decision
Option A.

**Pipeline (public data only)**
1. Performers: from the source when given (schema.org `performer`, Ticketmaster attractions), else split from the title on clear separators.
2. Identification: Deezer search, **exact normalised name match only** — no fuzzy guess, so a wrong homonym is less likely than a miss.
3. Enrichment: Deezer fans count and up to 20 related artists (co-listening signal); MusicBrainz style tags for the same name (search score 100).
4. Output: `data/artists.json` (key = normalised name) alongside `concerts.json`.

**Browser**
1. Profile = seed artists typed by the user, optionally imported from a public ListenBrainz account; feedback ("pertinent" / "pas pour moi"). Stored in `localStorage` only.
2. Seed style tags fetched from the MusicBrainz API from the browser (≤ 1 request per second).
3. Score per concert = best of its artists: exact seed match (1.0), related-artist overlap (0.8), style-tag cosine similarity (≤ 0.6), adjusted by feedback. Each match shows its reason ("Tu écoutes X", "Proche de X", "Style : …").
4. "Écouter" loads the official Deezer widget only on click; "Partager" opens a WhatsApp link (no API).

## Security
- **Data:** the taste profile never leaves the browser except as artist names sent to public APIs the user triggers (MusicBrainz tags, ListenBrainz import). No analytics, no server-side storage.
- **Untrusted input:** API data and scraped titles rendered with `textContent` only; Deezer ids must match `^[0-9]+$` before any URL is built; usernames URL-encoded.
- **Third parties:** the Deezer iframe is loaded only on an explicit click (no tracking on page load).
- **Secrets:** none added (Deezer and MusicBrainz are keyless).
- **AI transparency:** no AI in ranking; the page says recommendations are computed from public data and the user's own choices.

## Licences
- MusicBrainz tags are supplementary data (CC BY-NC-SA 3.0): attribution on the page; non-commercial use only — a commercial launch needs a MetaBrainz licence or another tag source.
- Deezer API: free, no paid tier; commercial terms unclear — fine for the POC, to revisit before any commercial use.

## Consequences
- Easier: personalisation for any visitor with zero setup cost and zero personal data on our side.
- Harder: no notifications based on taste yet (needs a server-side profile, later ADR); profile tied to one browser.
- To watch: identification miss rate (titles without clean performer names), MusicBrainz and Deezer rate limits in CI, CORS availability of MusicBrainz / ListenBrainz from the browser.

## Cost impact
€0/month. Total vs budget: €0 / €20.

## Amendment (2026-10-06, WIP-40): confident identities only
Exact-name matching produced homonyms ("Sheldon + Lupi'o + Asna" shown as "Proche de Acid Arab").
Related artists, style tags and the "Découverte" badge now come only from *confident* identities:
a single exact-name artist on Deezer and on MusicBrainz, a name of ≥ 4 characters and ≥ 1,000
Deezer fans. Other artists keep their identity for exact matches with the listener's own artists.
Trade-off: less recall on small local acts, in exchange for precision (William's feedback).
