# ADR-0005: Event store and server-side feedback (Scaleway, EU)

- **Status:** ✋ Accepted (William, 2026-10-06) — Scaleway account and API key: William
- **Amended by:** [ADR-0007](0007-private-taste-judgements.md) (2026-10-09): with a send key, the page reads taste judgements through the feedback function (`GET /verdicts`) while browsing.
- **Date:** 2026-10-06
- **Deciders:** William (PM), Claude (engineer)

## Context
- The pipeline is stateless (ADR-0001): every night it rebuilds three JSON files from scratch.
  De-duplication rules (WIP-42) work within one run, but nothing is remembered across runs:
  no history per source, no stable record when a source comes or goes, no place to keep
  manual corrections ("wrong match", "not a concert").
- Feedback ("J'aime", "Pas pour moi", "Mauvais rapprochement") lives in the browser's
  localStorage (ADR-0002). William wants it sent to the service directly, without exporting
  (2026-10-06), so the pipeline and the taste evaluation can use it.
- GitHub Pages only serves files: it cannot receive anything. Feedback is personal data, so
  the project rule applies: EU provider, minimal data. Budget ~€20/month.

## Options considered
| Option | Pros | Cons | Monthly cost (excl. VAT) |
|---|---|---|---|
| **Scaleway Serverless SQL Database (PostgreSQL) + Serverless Function** | French provider, EU region; plain PostgreSQL; not billed when idle (min 0 vCPU); functions free tier | Account + payment method needed; cold start of a few seconds when the database wakes up | ≈ €1–2 (see below) |
| Scaleway Managed Database, smallest (DB-DEV-S, 2 vCPU / 2 GB) | Always on, no cold start | Always billed | ≈ €11.7 + VAT |
| Supabase (Frankfurt region) | Free tier, auth and REST included | US company (CLOUD Act) for personal data; free projects pause after a week idle | €0 |
| Keep localStorage + manual export | Nothing to run | What William does not want; no store for de-dup history | €0 |

Cost estimate for the recommended option, from the Scaleway price list (2026-10-06):
vCPU €0.13752 per vCPU-hour, storage €0.000272 per GB-hour (≈ €0.20 per GB-month), daily
backups kept 7 days free. "You will not be billed if your database is configured with 0 vCPU
as a minimum and is in idle status"; storage is always billed. Activity ≈ 25 min/day (nightly
pipeline + a few page sessions) ≈ 12–13 h/month at 0.5–1 vCPU → **€0.9–1.8**; storage
< 0.1 GB → €0.02. Serverless Functions: free tier of 1,000,000 requests and 400,000 GB-s per
month, min scale 0 → **€0**. Watch item: the inactivity delay before the database goes idle is
not documented; the first month's invoice is checked against this estimate.

## Decision
Scaleway, region fr-par:
1. **Serverless SQL Database (PostgreSQL)**, autoscaling min 0 / max 1 vCPU. Tables:
   `raw_events` (source, source id/url, payload, first/last seen), `concerts` (canonical record,
   stable id + aliases, links), `concert_sources` (raw → canonical, rule that matched),
   `overrides` (manual merge/split/not-a-concert), `feedback` (concert id, artist key, kind,
   created_at).
2. **The nightly pipeline** writes raw events, runs the de-dup rules (dedup.py) against the
   stored canonical records so ids stay stable, applies overrides and feedback, then exports the
   same static JSON files to GitHub Pages. The page keeps reading static files: no database
   call when browsing.
3. **One Serverless Function** `POST /feedback`: validates a small JSON body (schema, size
   limit, allowed kinds), writes one row. Authentication: a personal write token, created by
   William and pasted once into the page settings, stored in that browser only; the function
   compares a hash. CORS limited to the Pages origin. Rate limit per token.
4. Local fallback: when the function cannot be reached, feedback stays queued in localStorage
   and is sent later.

## Security
- **Data:** only William's ratings (concert id, artist key, kind, timestamp) — no name, no
  e-mail, no IP stored. EU provider and region. GDPR: personal use by the data subject himself;
  deletion = one SQL statement.
- **Secrets:** database credentials and the token hash in Scaleway secrets / GitHub Actions
  secrets (`SCW_ACCESS_KEY`, `SCW_SECRET_KEY`, `SCW_DEFAULT_PROJECT_ID`, `FEEDBACK_TOKEN_SHA256`; the database URL is built at run time, not stored); never in the repo or the page.
- **Untrusted input:** the function validates against a JSON schema and uses parameterised SQL;
  source payloads are stored as data and never executed.
- **Supply chain:** psycopg pinned; Scaleway's official function runtime.
- **Logs:** no request bodies, no tokens.

## Consequences
- Easier: stable concert ids, de-dup across runs, manual corrections, server-side feedback that
  the nightly ranking and the taste evaluation (WIP-41) can use.
- Harder: one more service and two secrets to manage; tests need a local PostgreSQL (CI service
  container); the pipeline must keep working (static export from the last run) if the database
  is down.
- Migration: one PR for the store + pipeline (no behaviour change), one for the function and
  the page.
- Implementation notes (WIP-44, function and page): the function accepts a batch (≤ 50 items,
  ≤ 4 KB) rather than a single row, so the page can flush its queue in one request; one row is
  stored per (item, artist key). The rate limit is 300 rows per minute (429 above; the page
  keeps the items queued). The token hash and the database URL are secret environment
  variables of the function (the URL is built at deploy time, as in the pipeline).
- Implementation notes (WIP-46, profile sync): migration `002_profile.sql` adds `profile`
  (one row `id='me'`, `data` JSONB, `updated_at`, `version`) and `profile_writes` (timestamps
  for the rate limit). The same function serves `GET /profile` (200 `{data, version,
  updated_at}`, or 200 `{data: null, version: 0}` when there is none, so that a gateway 404
  is never read as "no profile") and `PUT /profile` `{data, base_version}`: written only if
  `base_version` equals the stored version (0 = no row yet), else 409 with the current
  `{data, version}`; same token, CORS (preflight allows GET and PUT), 64 KB body, strict
  schema (seeds ≤ 200 with name ≤ 60 chars and ≤ 12 tags; lists ≤ 2000 keys or concert ids,
  no other field), 60 PUTs per minute (429). The page reads the profile on load (the server
  copy wins unless this browser holds unsent changes, which are merged as a union), then PUTs
  it 1.5 s after each change; on 409 it merges (union of lists, server seeds then new local
  ones; a key liked on one side and disliked on the other keeps this browser's choice) and
  retries once. « Tout effacer » overwrites the server copy (no merge), including when it is
  pressed while a PUT is in flight (the page compares a snapshot taken before sending).
  Known limitation of the union: a removal (un-like, un-hide, a seed deleted) made on one
  device comes back when it meets a concurrent change from another device; « Tout effacer »
  is the way to start over. The profile
  holds the listener's choices only, never the Spotify token (ADR-0003). Data scope grows from
  ratings to seed artist names and style tags: still the listener's own data, same provider.
  Route detection reads the event's `path` (documented by Scaleway:
  https://www.scaleway.com/en/docs/serverless-functions/reference-content/code-examples/)
  or `rawPath` (unverified: the Scaleway event fields; `httpMethod` is measured — the
  2026-10-06 deploy answered the preflight with 204 and a wrong-token POST with 401). The
  deploy smoke test now also requires, with a wrong token, `GET /profile` → 401 and
  `GET /` → 405, which proves sub-paths reach the function; the deploy fails otherwise.
- Implementation note (WIP-73, written taste): the profile gains `taste_text` (« Mon goût
  en mots », PRD FR-4: a string of at most 4,000 characters, no NUL because PostgreSQL's
  jsonb rejects `\u0000`, https://www.postgresql.org/docs/current/datatype-json.html) and
  `taste_text_at` (the edit time in ms, an integer ≤ 2^53 − 1); a text that is not valid
  Unicode (a lone surrogate, not encodable as UTF-8) gets 400. The text is not a list, so the
  page merges it last-writer-wins: the copy with the more recent `taste_text_at` is kept,
  this browser's on a tie. The function applies the same rule on the row it has locked
  (`SELECT … FOR UPDATE`): a PUT without the field (a page older than this change) keeps the
  stored text, and a PUT with an older `taste_text_at` does not replace it, so only an
  explicit `""` with an edit time at least as recent clears it (tests
  `test_profile_save_without_taste_text_keeps_the_stored_text`,
  `test_profile_explicit_empty_taste_text_with_newer_time_clears_it`, and the real-Postgres
  test in CI). Known limits: the time is each device's clock, and the losing text is
  replaced, not combined. Size: the text alone is at most about 24 KB of JSON (4,000 escapes
  of 6 bytes), and fits the 64 KB body with both id lists full (test
  `test_profile_worst_case_taste_text_fits_the_body_limit`); seeds (200 × 12 tags of 100
  characters) and the artist key lists (2,000 keys each) are not counted there, so a large
  profile can exceed 64 KB: the function answers 400 "body too large" and the page shows
  « Profil non synchronisé : profil trop volumineux » (test in `tests/js/profile.test.js`).
  Personal data: never logged (test `test_taste_text_is_never_logged`), never in the repo,
  same EU provider.
- Implementation note (WIP-59, saved concert ids): `hidden` and `likedConcerts` are never
  pruned because an id is absent from a day's data (source failure, id change, past concert);
  only the display resolves current ids, and a saved alias stays next to its current id. The
  page keeps the newest 500 ids per list (oldest dropped first). The function's limit for
  these two lists goes from 2000 (the WIP-46 limit, still used for key lists) to the same
  500; measured, 500 ids are 7,613 bytes of JSON, so both lists use under a quarter of the
  64 KB body (tests in `tests/js/keep.test.js`, `tests/test_feedback_function.py`).
- Implementation notes (WIP-46, pipeline ↔ store): `store/sync.py`, called by the pipeline
  when a database URL is available. In CI, `nightcrawler run --store` builds the URL and
  migrates inside the run step itself, so the URL is never exported to later steps (missing
  secrets: notice; store failure: warning with the error type only). One connection (60 s
  connect timeout for a waking database, libpq TCP keepalives), client encoding forced to
  UTF-8 (a local SQL_ASCII server returned bytes before this: measured), one transaction
  with `SET LOCAL statement_timeout = '60s'`, before artist enrichment. Raw events are
  upserted (`source_key` = URL + title + start: sources expose no id yet). Stored concerts
  of the window are matched by exact id, then by id or alias, then by
  `dedup.same_concert_across_runs()`: same slot and place, and a shared performer when both
  sides name performers, else equal cleaned titles. Word overlap alone never matches across
  runs, because series words cannot be computed from two listings: the review of PR #40
  showed "Nuits Sonores: Boris" / "Nuits Sonores: Earth" and three similar pairs matching
  with the within-run rule (tests in `tests/test_store_sync.py`). A rule match is also used
  only when it is unique on both sides. `concert_sources.rule` = `new` | `id` | `dedup`, the
  rule that first attached that raw event. `not_concert` overrides (payload `{concert_id}`,
  id or alias) drop concerts from the output; `merge`/`split` are WIP-50. Artist keys with a
  `wrong` feedback are published with no related artists or tags and `doubt: "reported"`.
  Privacy: `artists.json` is public, so it shows which artist keys the listener reported as
  wrong matches (no date, no concert, no person; the listener's own data, as for the profile
  above). Any store error (refused connection, statement timeout, unexpected encoding: all
  tested) keeps this run's own result and is reported as `store.status = "error: <type>"`.
  Tested on a local PostgreSQL 16 (two runs keep the id, a renamed title with the same
  performer keeps it, an override drops, a wrong feedback is returned), on UTF-8 and
  SQL_ASCII databases; not yet run against the Scaleway database (unverified until the
  first Pipeline run).
- Follow-up WIP-45: the function connects with the pipeline's API key for now; a dedicated IAM
  application with Serverless SQL rights only will replace it.

## Cost impact
≈ €1–2/month excl. VAT, inside the ~€20 budget (current total: €0 + this).
The function is public: unauthenticated calls (refused with 401/403 before any database
access) still count as invocations, which the free tier (1,000,000 requests/month) covers.
Actions for William: create the Scaleway account (fr-par), a project and an API key, add
`SCW_ACCESS_KEY`, `SCW_SECRET_KEY`, `SCW_DEFAULT_PROJECT_ID` as GitHub secrets.
