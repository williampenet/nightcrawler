# ADR-0008: Public read-only showcase: published picks and profile, visitor mode

- **Status:** ✋ Accepted (William, 2026-10-12, chat: « Tes choix, publics » and « Ton profil en lecture »). Amends ADR-0005 and ADR-0007.
- **Date:** 2026-10-12
- **Deciders:** William (PM), Claude (engineer)
- **Ticket:** WIP-118

## Context
The page is public (GitHub Pages). The PM asked (2026-10-12 08:05) that visitors other than him
may only browse the first two tabs (Concerts, Calendrier) and open « Page », « Billets » and
« Écouter »; « Mes goûts » stays visible but allows no action.

What a visitor could do before this ADR (read in the code, `web/app.js` at f4b3db9):
- Every write and the judgements need the send key: `POST /` (ratings), `PUT /profile`,
  `GET /profile`, `GET /verdicts` answer 401 without it (ADR-0005, measured on the 2026-10-06
  deploy; ADR-0007 §4). A visitor's clicks only changed **their own browser's** `localStorage`,
  never William's data.
- Without the key the page gets no verdicts (ADR-0007) and the visitor's profile is empty, so
  « Pour toi » showed the "Dis-moi ce que tu aimes" hint and « Mes goûts » an empty form: the
  showcase showed none of the product's value.

So the request has two parts: hide the actions (interface), and decide what a visitor sees.

## Options considered
| Option | What visitors see | Privacy | Cost / complexity | Verdict |
|---|---|---|---|---|
| A. Hide actions only, keep picks private | Concerts without « Pour toi », empty « Mes goûts » | Unchanged | Page only | Rejected by the PM: the demo shows no recommendation |
| B. Public read route on the function (`GET /verdicts` and `/profile` without key) | William's picks and profile, live | Public | Each visit wakes the database (cold start, ADR-0005); rate limiting for anonymous traffic | Not chosen |
| **C. The pipeline publishes `data/verdicts.json` and `data/profile.json` with the site** | William's picks and profile as of the last run | Public | Two files, no new route, no database call when browsing (ADR-0005's principle) | **Chosen** |

C's only drawback is freshness: a visitor sees the profile and picks of the last Pipeline run
(two runs a day); William himself, with his key, keeps the live reads of ADR-0005 / ADR-0007.

## Decision
1. **Publication (PM decision, 2026-10-12).** After judging, the pipeline reads in one read-only
   transaction the stored verdicts of the published concerts (ids and aliases) and the profile,
   and writes:
   - `data/verdicts.json`: `{generated_at, verdicts: {id: {section, verdict, confidence,
     reason}}}`, the shape of `GET /verdicts` (ADR-0007 §4), so the page parses both alike;
   - `data/profile.json`: `{data: {seeds, liked, disliked, wrong, likedNames, dislikedNames,
     hidden, likedConcerts, taste_text}}`. Every list is needed for the visitor to see the same
     selection as William (known-artist rule WIP-88, hidden concerts, scoring). The edit time
     `taste_text_at` is not published (not needed).
   When the store is unavailable the files are not written; the page then says the selection is
   unavailable.
2. **Two modes in the page.** Owner mode: a send key is stored and the server has not refused it
   (a 401 on any route switches back to visitor mode). Visitor mode otherwise.
3. **Visitor mode.** State and judgements come from the two public files; nothing of them is
   written to the visitor's `localStorage`. Kept: navigation (bottom tabs, views, periods, sort,
   filters, calendar months and days, details disclosure), « Page », « Billets », « Écouter ».
   Not rendered: J'aime, Pas pour moi, Mauvais rapprochement, Partager, WhatsApp, À trier,
   « Saisir la clé ». « Mes goûts »: William's artists and « Mon goût en mots » in read-only
   fields; no button (Spotify, Enregistrer, Tout effacer, ListenBrainz). Controls are not
   rendered rather than hidden by CSS, so they are absent for keyboards and screen readers too.
4. **Owner entry.** No visible "log in": the key field appears when a key is stored or when the
   page is opened at `#cle` (« Mes goûts » with the key field focused).
5. **Real protection stays server-side** (ADR-0005): the visitor mode is an interface
   guarantee, not a security boundary. Typing a wrong key at `#cle` gives a 401 and visitor mode.

## Security
- **Data:** the PM chose to publish his music-taste profile, ratings and the judge's verdicts and
  reasons (his own personal data, published at his explicit request; not legal advice). No data of anyone else is in these files. Nothing else from the store is published:
  no listing descriptions, no feedback timestamps, no send key. Withdrawal: a code change
  (stop writing the files) and the next run removes them from the site; earlier copies may
  remain in caches or archives (to know before publishing).
- **Untrusted output:** reasons are model output; they keep ADR-0007's validation (schema,
  ≤ 240 characters) and the page parses the public file with the same `verdicts.parse` and
  renders text only, with the "IA" label (AI Act).
- **Secrets:** unchanged; the key is never in the files or the repo.
- **Logs:** counts only (number of verdicts published, profile present or not).

## Consequences
- The showcase shows the product's value (picks with reasons) to anyone, without an account.
- ADR-0007's "verdicts stay private" and its "no publication" test are amended: verdicts now
  reach `site/data/verdicts.json` only; `concerts.json` and `report.json` still carry no
  verdict or reason (test kept).
- Visitors see the data of the last run, not live edits.

## Cost impact
None: two static files on GitHub Pages; one extra read-only query per run. Total unchanged.
