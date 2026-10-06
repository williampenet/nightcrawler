# PRD – Nightcrawler

> **v3 change (2026-10-06, draft for PM validation):** the PM compared Nightcrawler with the
> concert-watch task he ran before (an LLM reading a fixed list of venues against a written taste
> profile) and found its picks better. Measured the same evening: of the 40 concerts that watch
> had flagged within Nightcrawler's 60-day window, Nightcrawler had **11 (27.5 %)**, and 5 of
> those 11 had no identified artist, so the taste scoring could not reach them (method in §1).
> v3 keeps the app as the deliverable and changes three things: **sources** (the PM's venues
> first, the rest of the zone kept open), **taste** (a written profile judged per concert by a
> language model, instead of artist similarity from Spotify), and **evaluation** (the old watch's
> picks become the reference set). Changes are marked "v3" below.

> **v2 change (2026-10-05):** Nightcrawler is now a personal app for William only, with easy sharing to friends. Multi-user requirements move to "Later". Spotify login becomes the taste source (Development Mode allows the owner plus up to 4 test users).

| | |
|---|---|
| Status | v3 draft — curated venues + written taste profile (2026-10-06, awaiting PM validation); v2 personal app validated 2026-10-05 |
| PM | William Penet |
| Author | Claude (from PM brief, discovery session of 2026-10-05) |
| Related | Experiment plan "Plan d'expérimentation – sorties culturelles" (90-day, Claude Doc) |

## 1. Problem

People who love live music miss concerts they would have enjoyed, because the information is scattered: venue websites, newsletters, ticketing apps, social media. Existing tools (Shotgun, Bandsintown, Songkick) mostly alert on artists you already follow; they do little to surface unknown artists close to your taste, and they cover small independent venues poorly.

The problem is sharpest for niche tastes (experimental, improvised, underground scenes): those concerts happen in small venues that are absent from big ticketing platforms, by artists with little streaming data.

Discovery has also become harder to build: since November 2024 Spotify no longer gives new apps access to related artists, recommendations or previews ([Spotify, 2024-11-27](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)), and in Development Mode it no longer returns artist genres, popularity or follower counts (measured 2026-10-06, see "Spotify: what the API returns" below).

**Evidence today: light** (the PM's own experience). The first validation step measures it (see FR-9, "missed concerts" retro).

**Evidence added 2026-10-06 (v3).** Before Nightcrawler, the PM ran a weekly concert watch: a
Claude task reading 11 venue sites and 2 agendas (Ville Morte, Petit Bulletin) against a written
taste profile, with a memory of what it had already reported (`concerts-vus.md`, 76 events from
2026-07-26 to 2026-12-17). The PM rates its picks as good and Nightcrawler's as mostly off-target.
Measurement on the published data of 2026-10-06 20:42: 40 of those events fall in Nightcrawler's
window (≤ 2026-12-05); 11 are present (artist name searched in title and performers, same date,
checked by hand). Missing venues: Opéra Underground (no website linked, 0 concerts), Les
Subsistances and Chapelle de la Trinité (no agenda detected), Grrrnd Zero (platform pages only,
0 concerts), Auditorium (2 concerts); L'Épicerie Moderne gives 4.
The gap has two causes: coverage (agendas published only as HTML are not read, ADR-0001 /
ADR-0004) and taste (the profile is described by criteria, not by artists — see FR-5).

## 2. Target users & jobs-to-be-done

- **Primary (V1)**: the PM, living in Villeurbanne (Lyon metropolitan area), eclectic and niche-heavy music taste, goes out regularly but feels he misses things.
- **Friends (recipients, not users)**: they receive concerts William shares (WhatsApp, Signal, SMS…) and open a link; they need no account.
- **Later, maybe**: other music lovers. Not designed for now (v2 decision).

Jobs-to-be-done:
1. *When a concert I would love is announced near me, I want to hear about it without searching, so I don't miss it.*
2. *When I see an artist I don't know, I want to understand in seconds why it's for me and hear it, so I can decide.*
3. *When I decide to go, I want to get a ticket and tell my friends in one tap, so going out becomes a shared plan.*

## 3. Goals & success metrics

Targets come from the experiment plan (validation phase, days 46–90).

| Goal | Metric | Target |
|---|---|---|
| The tool comes to me | Weekly digests opened | ≥ 60 % |
| Recommendations are relevant | Share of "relevant" feedback in the digest after 4 weeks | ≥ 50 % |
| I discover new artists | Unknown artists rated relevant per digest | ≥ 1 |
| I actually go out more | Concerts attended thanks to the tool | ≥ 1 per month |
| It beats what I already use | Relevant concerts surfaced only by the tool (vs Shotgun, Bandsintown, newsletters) | ≥ 30 % |
| Urgent alerts are worth it | Useful alerts / ignored alerts | ≥ 1 useful per month, ≤ 2 ignored per week |
| It runs on its own | Maintenance time / running cost | ≤ 30 min per week / ≤ 20 € per month |

Feasibility gates (week 1 spikes, must pass before the build): venue coverage ≥ 80 % of a reference list, artist identification precision ≥ 90 % above the confidence threshold.

## 4. Scope

### In scope (MVP)
- **Zone**: Lyon metropolitan area, configurable as a centre point + radius (no hard-coded city).
- **Sources in two circles** (v3): **"Mes salles"**, the PM's list of venues and agendas (config, editable), each read completely, by structured data when published, else by a language model reading the page; **"Ailleurs"**, the rest of the zone, still discovered automatically (aggregators, maps, ticketing), shown only when the taste judgement is strong. A user can add a venue by pasting its URL.
- **Event collection**: daily refresh, de-duplication across sources.
- **Artist identification**: each billed name linked to a canonical artist, with a confidence score.
- **Taste profile** (v3): a written profile in the PM's words (what to favour, what to set aside, reference artists), editable in the app; Spotify artists and in-app ratings complete it (FR-4).
- **Taste judgement** (v3): each new or changed concert is judged against the profile by a language model chosen by ADR, with a one-sentence reason (FR-5).
- **Views** (v3): the app is the deliverable. Home shows "À ne pas rater", "Pour toi", "Découvertes", then everything; list and calendar; concert page with reason, ticket link, share.
- **Notifications** (v3, Should): an alert when a "À ne pas rater" concert is announced; the weekly email digest is dropped (PM, 2026-10-06: "le livrable, c'est l'appli").
- **Feedback**: "relevant / not for me" on each concert, used to tune the score.
- **"Missed concerts" retro**: after 4 weeks, a 2-minute review of past matching concerts.
- **Sharing (first-class)**: the phone's native share sheet (WhatsApp, Signal, SMS, mail…), copy link, deep link to a concert (FR-10).
- **Mobile-first installable web app (PWA)**, also usable on desktop.

### Out of scope
- Theatre and other outings (V2: the model is designed to accept other event types).
- Geolocation / "where I am now" mode (V2).
- Social features: friends' plans, "I have my tickets" status (V2).
- Ticket affiliation and any monetisation.
- Native mobile apps; WhatsApp Business API.
- Buying tickets inside the app (links only).

## 5. User stories

| ID | As a… | I want… | So that… | Priority |
|---|---|---|---|---|
| US-01 | user | to connect my Spotify account once | my tastes are known without typing them | Must |
| US-02 | user | a weekly digest of the best upcoming concerts for me | I don't have to search | Must |
| US-03 | user | an immediate alert when an artist I love announces a date or when a matching concert is selling out | I don't miss tickets | Should |
| US-04 | user | to see why a concert is recommended | I trust the pick and decide faster | Must |
| US-05 | user | to listen to an extract on the concert page | I can judge an unknown artist in seconds | Must |
| US-06 | user | a direct link to the official ticket page | I can buy in one tap | Must |
| US-07 | user | to share a concert in two taps on WhatsApp or any app | I can rally friends | Must |
| US-08 | user | to browse concerts as a list and as a calendar | I can both discover and plan | Must |
| US-09 | user | to mark a concert "relevant" or "not for me" | the next picks get better | Must |
| US-10 | user | to add a venue by pasting its URL | the tool covers places it missed | Should |
| US-11 | user | to flag a wrong artist match | mistakes get fixed | Should |
| US-12 | user | a short review of concerts I may have missed | I can tell whether the tool solves my problem | Should |

## 6. Functional requirements & acceptance criteria

**FR-1 Venue discovery (US-01, US-10)** — v3: two circles
- **"Mes salles"** (config `config/zone.yaml`, PM's list): Le Périscope, Le Sonic, Le Marché Gare, L'Épicerie Moderne, Le Transbordeur, Grrrnd Zero, La Rayonne (CCO), Auditorium de Lyon, Chapelle de la Trinité, Opéra Underground, Les Subsistances; agendas Ville Morte and Petit Bulletin. Each is read completely: structured data first, else the page text through the `extract_events` task (ADR-0004, to be re-run with stronger candidates).
- AC (v3, gate): ≥ 80 % of the reference events (FR-11) in the window are present, measured in CI on each run; each venue of "Mes salles" reports its status (read / broken) in the app.
- **"Ailleurs"**: the existing automatic discovery below stays, for openness; its concerts reach the home sections only through the taste judgement.
- Venues are found automatically from three layers: event aggregators (Ticketmaster Discovery API, OpenAgenda, DATAtourisme), map data (OpenStreetMap venue categories), and a probe of each venue website that detects structured data (schema.org Event, iCal, RSS, ticketing widgets), with an LLM extraction fallback.
- AC: on the PM's reference list of venues (including small independent ones), ≥ 80 % are found and connected with no manual step.
- AC: adding a venue by URL connects its agenda automatically, or reports clearly why it could not.

**FR-2 Event collection**
- Daily refresh of every connected source; the same concert from several sources is merged into one event.
- AC: no duplicate concerts in the feed on a test week; ≤ 1 broken collector per week, detected automatically.

**FR-3 Artist identification (US-11)**
- Each billed name is resolved to a canonical artist (MusicBrainz ID as pivot), using links published by the venue, catalogue searches (MusicBrainz, Deezer), country and venue-size consistency, and a confidence score.
- AC: ≥ 90 % precision above the confidence threshold, measured on concerts where the venue itself links the artist; below the threshold, the artist is shown as "to confirm".

**FR-4 Taste profile (US-01)** — v3
- **The written profile is the main taste source**: favoured families with reference artists, what to set aside ("ce qui est formaté grand public… l'évident, le consensuel, le déjà-entendu"; notoriety is not the criterion), and conditions ("jazz only when it crosses something else"). It starts from the PM's watch instructions, is editable in the app and is stored in the event store, not in the public repo.
- Spotify and ratings stay as complements (below).

v2 text, kept as complement:
- Spotify login in the browser (OAuth Authorization Code + PKCE, no server, no secret): top artists (short, medium, long term) and followed artists become the seeds, then the artists of liked tracks (up to 500) and recently played tracks, most frequent first (WIP-49; endpoints verified by the 2026-10-06 test below); only artist names are kept (≤ 150), the token is never stored.
- Seeds can be edited by hand; a public ListenBrainz account can also be imported.
- AC: one click on "Connecter Spotify" fills "Mes goûts"; the "Pour moi" sort works right after.

**FR-5 Taste judgement (US-04)** — v3
- Task `judge_taste` (one task, one model, CLAUDE.md): input = concert text (title, performers, venue, the venue's own description when available) + written profile + a few of the PM's rated examples; output, schema-validated = `{verdict: must_see | for_you | discovery | no, reason (one sentence, French), confidence}`.
- Model chosen by a model-selection ADR: shortlist from the [QuelLLM.fr catalogue](https://quelllm.fr/catalogue), ≥ 3 open-weight candidates (European, Chinese or other; licence checked on the model card) + Claude as proprietary baseline, measured on FR-11. If no candidate is close enough to the baseline, a small model may be adapted to the task (fine-tuning, e.g. LoRA) — only with its own ADR (method, data, cost).
- Only new or changed concerts are judged (cached by content hash); verdicts are stored.
- The UI marks reasons as AI-generated (AI Act).
- AC: on FR-11, the chosen model's "must_see + for_you" recall ≥ 80 % of reference positives, with precision reported against the PM's labelled negatives; every judged concert shows its reason.

v2 text (artist similarity), kept as a complementary signal for concerts with an identified artist:
- A cascade according to what is known about the artist: co-listening similarity (ListenBrainz, Deezer) for known artists; audio similarity of extracts for lesser-known ones; references extracted from the venue's text ("in the vein of…"), co-billing and venue affinity for the rest. Signals are combined into one score; weights are tuned by user feedback.
- ~~No LLM is used to judge musical similarity; LLMs only extract information from text.~~ Superseded by v3: the judgement is against a written profile, not artist-to-artist similarity, and it is measured on FR-11 before use.
- AC: on the backtest (hidden artists of the listening history), the combination beats the best single signal; every recommendation shows at least one human-readable reason.

**FR-6 Feed and concert page (US-05, US-06, US-07, US-08)** — v3: home = "À ne pas rater" (1–4 per week, verdict `must_see`), "Pour toi", "Découvertes" (artists unknown to the profile, in a relevant venue), then "Tout voir"; a "nouveau" badge on concerts added since the last visit (replaces the watch's memory of reported events).
- List view sorted by score, calendar view by date, filters by date and venue.
- Concert page: date, venue, artists, reason, audio preview (Deezer extracts), official ticket link, share button (FR-10).
- AC: from the digest, reaching a playing preview takes ≤ 2 taps.

**FR-7 Notifications (US-02, US-03)** — v3: the weekly email digest is dropped (the app is the deliverable); an alert for a new `must_see` concert stays a Should, channel to decide.
- Weekly email digest (top picks + at least one discovery).
- Immediate Telegram alert for: a new date by a strongly matched artist, tickets going on sale, a matching concert close to sold out (when the source exposes it).
- AC: alerts sent within 24 h of detection; user can mute alert types.

**FR-8 Feedback (US-09)**
- "Relevant / not for me" on every concert, in the app and from the digest; implicit signals (preview played, calendar add, ticket click) are logged.
- AC: feedback is stored per user, feeds the evaluation set and changes the ranking of later picks.

**FR-9 "Missed concerts" retro (US-12)**
- After 4 weeks of collection, the app lists past concerts that matched the profile; the user answers "I knew / I missed it / not for me".
- AC: completable in ≤ 2 minutes; results measure the problem (target: ≥ 3 missed per month, "didn't know" ≥ 50 % of causes).

**FR-10 Sharing (US-07)** — v2
- "Partager" opens the device's native share sheet (WhatsApp, Signal, SMS, mail…); on desktop, a WhatsApp link and "Copier le lien".
- The message holds title, venue, day and time and a link: the event page when there is one, else a Nightcrawler link that opens the page on that concert.
- AC: sharing takes ≤ 2 taps on a phone; a friend opening the link sees the concert without any account.

**FR-11 Reference set and evaluation (v3)**
- Positives: the events of the PM's former watch (`concerts-vus.md`, 76 events, 2026-07-26 → 2026-12-17) plus "J'aime" ratings. Negatives: "Pas pour moi" ratings, plus a sample of concerts at the same venues that the watch did not report, **labelled by the PM** (a watch omission is not a "no" until he says so).
- Used for two measures, re-run in CI: source coverage (FR-1 gate) and judgement quality (FR-5, MODEL_EVAL).
- AC: the set is versioned (public events and labels only, no profile text); coverage and judgement scores appear on each run.

## 7. Non-functional requirements

- **Cost**: ≤ 20 € per month in total, free tiers first.
- **Privacy (GDPR)**: listening history and feedback are personal data, stored and processed in the EU; no personal data sent to a non-EU provider; provider retention and training disabled; data deletion on request.
- **LLM usage** (v3): two tasks, `extract_events` (agenda pages) and `judge_taste` (concert vs profile), each with its own model-selection ADR; candidates studied from the QuelLLM.fr catalogue and verified on model cards; smallest adequate model, local / CPU first, then EU-hosted API; outputs validated against a schema; web content treated as untrusted data (prompt injection). The taste profile is personal data: it goes only to a local model or an EU provider with retention and training off; the proprietary baseline is used only in the offline evaluation, on public events and a profile text with no identity.
- **Sourcing etiquette and licences**: respect robots.txt and rate limits, cache responses; attribute ODbL sources (OpenAgenda, OpenStreetMap); check share-alike and non-commercial clauses before any public or commercial use.
- **Transparency (AI Act)**: the UI says recommendations are automated and shows why.
- **Freshness**: daily collection; alerts within 24 h.
- **Accessibility**: WCAG 2.1 AA on the main flows.
- **Language**: UI in French; code and docs in English.
- **Single user (v2)**: built for William; zone and data sources stay in config, so another city or user remains possible later without a rewrite.

## 8. Risks & open questions

| Risk / question | Impact | Mitigation / next step |
|---|---|---|
| Small independent venues are missed by aggregators | Core value lost for niche tastes | Map layer + website probe; measured in spike E2 |
| Non-commercial licences (Essentia audio models, MusicBrainz tags) | Blocks a future commercial use | Fine for personal, free use; flag in architecture ADR |
| ODbL share-alike on OpenAgenda / OpenStreetMap derived data | Obligations if the database is published | Legal check in architecture ADR (not legal advice) |
| Deezer API terms unclear for commercial use | Preview and co-listening source at risk | Abstract the source; ListenBrainz as fallback |
| Bandcamp blocks automated access | Fewer tags / extracts for indie artists | Not relied on in V1 |
| Access to the PM's listening history database | Blocks the V1 profile | Read-only credentials provided at build time |

## 9. Challenge log

| Brief said | Engineer pushed back | Resolution |
|---|---|---|
| Use Spotify for tastes, similar artists and previews | Spotify API closed these to new apps (2024–2026) | Spotify only as a history source; ListenBrainz, Deezer, MusicBrainz instead |
| Notify on WhatsApp | WhatsApp Business API is paid and heavy | Email digest + Telegram alerts; WhatsApp only as a share link |
| Mobile or desktop | Native apps cost two codebases and a store | Mobile-first PWA |
| Match concerts by music style | Style is coarse and missing for small artists | Combined score: co-listening, audio, venue text, co-billing |
| Let an LLM estimate taste proximity | Research finds LLM similarity judgements unreliable (never measured on this task) | LLM limited to text extraction — **reversed in v3**: the PM's LLM watch gave better picks; the judgement is against a written profile and is measured on FR-11 before use |
| Provide my list of venues | Manual and user-specific | Automatic discovery; the PM's list becomes the test set — **v3**: the list was never collected; it becomes the first circle of sources, discovery stays for the rest |
| Manual labelling to evaluate | Does not scale to other users | Backtest on listening history + in-app feedback |
| Keep a 4-week diary of missed concerts | Manual effort | Automated 2-minute "missed concerts" retro |
| One weekly notification | Some concerts need fast reaction | Two rhythms: weekly digest + immediate alerts |
| "No manual setup, works for anyone" (v1) | Spotify login caps at 5 users and needs Premium; a generic app cannot use it | PM pivot (v2): personal app, Spotify login for William, sharing to friends instead of multi-user |

| (v3) Deliver a weekly report like the old watch | — | PM: the app is the deliverable; the watch's memory becomes a "nouveau" badge |
| (v3) Keep Claude as the judge | Project policy: study open-weight models first | PM: study the catalogue (QuelLLM.fr), compare with Claude, adapt weights if needed; keep Claude only if the ADR shows it is needed |

## Spotify: what the API returns (measured 2026-10-06)

| Data | Call | HTTP | What came back |
|---|---|---|---|
| Top artists | `GET /me/top/artists` | 200 | items; **no `genres`, no `popularity`, no `followers` field** |
| Top tracks | `GET /me/top/tracks` | 200 | items; no `preview_url` |
| Followed artists | `GET /me/following?type=artist` | 200 | items |
| Recently played | `GET /me/player/recently-played` | 200 | items |
| Saved tracks | `GET /me/tracks` | 200 | items |
| Playlists | `GET /me/playlists` | 200 | items |
| Search, limit 10 | `GET /search` | 200 | 10 items |
| Search, limit 20 | `GET /search` | **400** | — |
| Artist | `GET /artists/{id}` | 200 | **no `genres`, no `popularity`, no `followers`** |
| Several artists | `GET /artists?ids=` | **403** | — |
| Related artists | `GET /artists/{id}/related-artists` | **403** | — |
| Artist top tracks | `GET /artists/{id}/top-tracks` | **403** | — |
| Recommendations | `GET /recommendations` | **404** | — |
| Audio features | `GET /audio-features` | **403** | — |

Sources: real test run by William on 2026-10-06 with the page's "Tester l'API Spotify" button
(WIP-48, PR #36; screenshot in the conversation), compared with Spotify's announcements
[2024-11-27](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api) and the
[February 2026 changelog](https://developer.spotify.com/documentation/web-api/references/changes/february-2026).
The changelog lists `followers` and `popularity` as removed; the missing `genres` field is
our measurement (not found in those pages).

Consequence: Spotify gives **names** (top and followed artists, plus artists of recently played, saved and playlist tracks), nothing about styles or similarity. Styles and similar artists keep coming from MusicBrainz and Deezer (ADR-0002, WIP-40).
