# ADR-0005: Event store and server-side feedback (Scaleway, EU)

- **Status:** Proposed — needs William's validation and a Scaleway account (human-only action)
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

## Decision (proposed)
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
  secrets (`SCW_DB_URL`, `FEEDBACK_TOKEN_SHA256`); never in the repo or the page.
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

## Cost impact
≈ €1–2/month excl. VAT, inside the ~€20 budget (current total: €0 + this).
Actions for William: create the Scaleway account (fr-par), a project and an API key, add
`SCW_ACCESS_KEY`, `SCW_SECRET_KEY`, `SCW_DEFAULT_PROJECT_ID` as GitHub secrets.
