# PRD – Nightcrawler

| | |
|---|---|
| Status | ✋ Validated on 2026-10-05 |
| PM | William Penet |
| Author | Claude (from PM brief, discovery session of 2026-10-05) |
| Related | Experiment plan "Plan d'expérimentation – sorties culturelles" (90-day, Claude Doc) |

## 1. Problem

People who love live music miss concerts they would have enjoyed, because the information is scattered: venue websites, newsletters, ticketing apps, social media. Existing tools (Shotgun, Bandsintown, Songkick) mostly alert on artists you already follow; they do little to surface unknown artists close to your taste, and they cover small independent venues poorly.

The problem is sharpest for niche tastes (experimental, improvised, underground scenes): those concerts happen in small venues that are absent from big ticketing platforms, by artists with little streaming data.

Discovery has also become harder to build: since November 2024 Spotify no longer gives new apps access to related artists, recommendations or previews, and since February 2026 it no longer exposes artist genres.

**Evidence today: light** (the PM's own experience). The first validation step measures it (see FR-9, "missed concerts" retro).

## 2. Target users & jobs-to-be-done

- **Primary (V1)**: the PM, living in Villeurbanne (Lyon metropolitan area), eclectic and niche-heavy music taste, goes out regularly but feels he misses things.
- **Secondary (V2+)**: his friends, then any music lover in a city. The V1 must not depend on manual, user-specific setup, so that opening to new users costs nothing.

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
- **Automatic venue discovery**: venues found from event aggregators and maps, their agendas detected and connected without manual setup; a user can add a venue by pasting its URL.
- **Event collection**: daily refresh, de-duplication across sources.
- **Artist identification**: each billed name linked to a canonical artist, with a confidence score.
- **Taste profile** from the user's listening history (V1 imports the PM's existing history; generic import paths for any user, see FR-4).
- **Affinity scoring**: a combination of co-listening, audio similarity and venue-text signals, explainable ("because it sounds like X").
- **Views**: list and calendar; concert page with audio preview, explanation, ticket link, share.
- **Notifications**: weekly email digest; immediate Telegram alerts for urgent cases.
- **Feedback**: "relevant / not for me" on each concert, used to tune the score.
- **"Missed concerts" retro**: after 4 weeks, a 2-minute review of past matching concerts.
- **Sharing**: a WhatsApp share link (no API).
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
| US-01 | new user | to give only my area and my listening history | I get recommendations without any other setup | Must |
| US-02 | user | a weekly digest of the best upcoming concerts for me | I don't have to search | Must |
| US-03 | user | an immediate alert when an artist I love announces a date or when a matching concert is selling out | I don't miss tickets | Should |
| US-04 | user | to see why a concert is recommended | I trust the pick and decide faster | Must |
| US-05 | user | to listen to an extract on the concert page | I can judge an unknown artist in seconds | Must |
| US-06 | user | a direct link to the official ticket page | I can buy in one tap | Must |
| US-07 | user | to share a concert on WhatsApp | I can rally friends | Must |
| US-08 | user | to browse concerts as a list and as a calendar | I can both discover and plan | Must |
| US-09 | user | to mark a concert "relevant" or "not for me" | the next picks get better | Must |
| US-10 | user | to add a venue by pasting its URL | the tool covers places it missed | Should |
| US-11 | user | to flag a wrong artist match | mistakes get fixed | Should |
| US-12 | user | a short review of concerts I may have missed | I can tell whether the tool solves my problem | Should |

## 6. Functional requirements & acceptance criteria

**FR-1 Venue discovery (US-01, US-10)**
- Venues are found automatically from three layers: event aggregators (Ticketmaster Discovery API, OpenAgenda, DATAtourisme), map data (OpenStreetMap venue categories), and a probe of each venue website that detects structured data (schema.org Event, iCal, RSS, ticketing widgets), with an LLM extraction fallback.
- AC: on the PM's reference list of venues (including small independent ones), ≥ 80 % are found and connected with no manual step.
- AC: adding a venue by URL connects its agenda automatically, or reports clearly why it could not.

**FR-2 Event collection**
- Daily refresh of every connected source; the same concert from several sources is merged into one event.
- AC: no duplicate concerts in the feed on a test week; ≤ 1 broken collector per week, detected automatically.

**FR-3 Artist identification (US-11)**
- Each billed name is resolved to a canonical artist (MusicBrainz ID as pivot), using links published by the venue, catalogue searches (MusicBrainz, Deezer), country and venue-size consistency, and a confidence score.
- AC: ≥ 90 % precision above the confidence threshold, measured on concerts where the venue itself links the artist; below the threshold, the artist is shown as "to confirm".

**FR-4 Taste profile (US-01)**
- V1: import of the PM's existing listening history.
- Generic paths for any user, none of which depend on the restricted Spotify API: the Spotify personal data export (GDPR file), a ListenBrainz or Last.fm account, or a short onboarding questionnaire of seed artists.
- AC: a profile can be built from each path without manual help.

**FR-5 Affinity scoring (US-04)**
- A cascade according to what is known about the artist: co-listening similarity (ListenBrainz, Deezer) for known artists; audio similarity of extracts for lesser-known ones; references extracted from the venue's text ("in the vein of…"), co-billing and venue affinity for the rest. Signals are combined into one score; weights are tuned by user feedback.
- No LLM is used to judge musical similarity; LLMs only extract information from text.
- AC: on the backtest (hidden artists of the listening history), the combination beats the best single signal; every recommendation shows at least one human-readable reason.

**FR-6 Feed and concert page (US-05, US-06, US-07, US-08)**
- List view sorted by score, calendar view by date, filters by date and venue.
- Concert page: date, venue, artists, reason, audio preview (Deezer extracts), official ticket link, WhatsApp share link.
- AC: from the digest, reaching a playing preview takes ≤ 2 taps; the share link opens WhatsApp with a prefilled message.

**FR-7 Notifications (US-02, US-03)**
- Weekly email digest (top picks + at least one discovery).
- Immediate Telegram alert for: a new date by a strongly matched artist, tickets going on sale, a matching concert close to sold out (when the source exposes it).
- AC: alerts sent within 24 h of detection; user can mute alert types.

**FR-8 Feedback (US-09)**
- "Relevant / not for me" on every concert, in the app and from the digest; implicit signals (preview played, calendar add, ticket click) are logged.
- AC: feedback is stored per user, feeds the evaluation set and changes the ranking of later picks.

**FR-9 "Missed concerts" retro (US-12)**
- After 4 weeks of collection, the app lists past concerts that matched the profile; the user answers "I knew / I missed it / not for me".
- AC: completable in ≤ 2 minutes; results measure the problem (target: ≥ 3 missed per month, "didn't know" ≥ 50 % of causes).

## 7. Non-functional requirements

- **Cost**: ≤ 20 € per month in total, free tiers first.
- **Privacy (GDPR)**: listening history and feedback are personal data, stored and processed in the EU; no personal data sent to a non-EU provider; provider retention and training disabled; data deletion on request.
- **LLM usage**: two narrow extraction tasks (venue agenda pages, artist references in venue texts), each with its own model-selection ADR, small European open-weight models preferred, outputs validated against a schema, web content treated as untrusted data (prompt injection).
- **Sourcing etiquette and licences**: respect robots.txt and rate limits, cache responses; attribute ODbL sources (OpenAgenda, OpenStreetMap); check share-alike and non-commercial clauses before any public or commercial use.
- **Transparency (AI Act)**: the UI says recommendations are automated and shows why.
- **Freshness**: daily collection; alerts within 24 h.
- **Accessibility**: WCAG 2.1 AA on the main flows.
- **Language**: UI in French; code and docs in English.
- **Multi-user readiness**: no user-specific configuration in code; data model scoped per user from day one, even if V1 has one user.

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
| Let an LLM estimate taste proximity | Research finds LLM similarity judgements unreliable | LLM limited to text extraction |
| Provide my list of venues | Manual and user-specific | Automatic discovery; the PM's list becomes the test set |
| Manual labelling to evaluate | Does not scale to other users | Backtest on listening history + in-app feedback |
| Keep a 4-week diary of missed concerts | Manual effort | Automated 2-minute "missed concerts" retro |
| One weekly notification | Some concerts need fast reaction | Two rhythms: weekly digest + immediate alerts |
