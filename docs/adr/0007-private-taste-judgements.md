# ADR-0007: Taste judgements are private: stored in the event store, served by the feedback function

- **Status:** ✋ Accepted (William, 2026-10-09 07:19, chat). Amends ADR-0005 and ADR-0006.
- **Date:** 2026-10-09
- **Deciders:** William (PM), Claude (engineer)
- **Tickets:** WIP-83 (store), WIP-84 (pipeline), WIP-85 (function), WIP-86 (page)

## Context
ADR-0006 routes `judge_taste` (Mistral Small 3.2, Scaleway Paris). Each judgement is a verdict,
a confidence and a one-sentence reason about William's taste ("du free jazz qui croise le noise,
comme tu aimes"). Taken together they describe his taste: **personal data** (GDPR Art. 4(1),
information relating to an identified person). The site (`site/data/*.json` on GitHub Pages) and
the repository are public, so judgements cannot be published there (CLAUDE.md, Security; PRD §7).

ADR-0005 keeps the page on static files ("no database call when browsing") and gives the feedback
function the listener's own choices (ratings, then the profile, WIP-46). ADR-0006 (Security)
describes the judge eval's store access as a read-only transaction.

## Options considered
| Option | Privacy | Cost / complexity | Verdict |
|---|---|---|---|
| A. Verdicts in `site/data/concerts.json` | Public: personal data on the open web | none | Rejected |
| B. Encrypted file in the site, key in the page | Public ciphertext; the key would sit in the browser and the repo's workflow | key management, no revocation | Rejected |
| C. Judge in the browser at view time | The model API key would be in the page | one call per concert per view | Rejected (ADR-0006: key in CI/host secrets only) |
| E. Private Object Storage JSON written by the pipeline, served or presigned by the function | Private, EU | a bucket, its credentials and presigning; the function still wakes on each load | Not chosen: the page already wakes the database on load with a send key (`GET /profile`, ADR-0005 WIP-46 note), so a table read adds one query, not a cold start; a bucket adds a second store to secure |
| **D. Pipeline writes to the event store; page reads through the authenticated function** | Stays in the EU store; needs the send key | one table, one route; DB woken when browsing | **Chosen** |

## Decision
1. **Judging runs in the Pipeline workflow** after artist enrichment, through `llm.run_task`
   (provider abstraction), only when a concert's input changed: the cache key is
   `judge.input_hash` (task, model, settings and the exact messages: concert, listing
   description, written taste, seeds, nearest ratings). The cache lives in the store, not in an
   Actions cache, because the prompts hold personal data. Deezer fan counts are rounded to two
   significant figures in the prompt so daily changes do not re-judge every concert.
2. **Storage:** table `verdicts` (migration 003): one row per concert (`concert_id`,
   `input_hash`, `verdict`, `confidence`, `reason` ≤ 240 characters, `section`, `model`,
   `starts_at`, `judged_at`); `section` is computed by `judge.section` (ADR-0006 rule) so the rule
   lives in one place, and recomputed from the stored verdict on every run, so a rule change
   needs no new model call; a CHECK ties the section to the verdict. The cache key covers the
   output schema too (it is sent with the request). Rows of concerts that started more than 7
   days ago are deleted on save: the page is served from yesterday on, and nothing else uses
   older judgements (data minimisation).
3. **Reads:** the pipeline reads profile, ratings, descriptions and known hashes in one read-only
   transaction (`store/verdicts.load_inputs`), closes it during model calls, then writes in a
   short transaction (`store/verdicts.save`). ADR-0006's "read-only" applies to the eval only.
4. **Serving:** the feedback function gets `GET /verdicts` (send key, origin check, as
   `/profile`), answering `{"generated_at", "verdicts": {id: {section, verdict, confidence,
   reason}}}` for concerts starting from yesterday on, `Cache-Control: no-store`; 401 / 403 /
   405 / 503 as the other routes. This amends ADR-0005: **with a send key, the page now reads the
   database while browsing** (one request per load). In practice the page already wakes it on
   load with a send key (`GET /profile`, ADR-0005 WIP-46 note); this adds one query. The page
   keeps a local copy to show at once and refreshes it in the background, which hides a cold
   start.
5. **Without a key or verdicts**, the page keeps the rule-based tiers (WIP-53). Concerts not yet
   judged (call cap, model failure) are shown in "Pour toi" marked "pas encore jugé": recall first
   (ADR-0006).
   **Amended 2026-10-09 by the PM (WIP-102):** « Pour toi » now lists only the concerts that very
   probably match (must-see, known-artist rule, the judge's « Pour toi »). Concerts not judged
   yet, Découvertes and the rest are in « Tout », where the must-see concerts stay visible as cards (WIP-106).
6. **Examples in production:** William's latest like / dislike per concert from `feedback`
   (`store/verdicts.read_ratings`, with its time: an unlike under an alias can undo a like under
   the current id), mapped to the published concerts (ids and aliases), latest first. The eval
   builds its labels with the taste eval's rules, which also drop ratings the profile no longer
   shows (stale, ambiguous: docs/TASTE_EVAL.md). Production can therefore use a few ratings the
   eval drops; the difference is not measured (**unverified**) and is reported as counts.

## Security
- **Data:** the model receives what ADR-0006 lists (EU provider, no retention, no training). The
  store is Scaleway Serverless SQL, Paris (ADR-0005). The function returns verdicts only to a
  request with the send key; nothing about verdicts is logged except counts.
- **Untrusted output:** reasons are model output: schema-validated (≤ 240 characters, enum
  verdict), stored as text, and the page must render them as text (no HTML), with an "IA" label
  (AI Act).
- **No publication test:** the pipeline's tests check that no verdict or reason reaches
  `site/data`.

## Consequences
- With a send key, browsing wakes the database (cold start, ADR-0005 measured the profile route);
  the local copy and the fallback tiers keep the page usable meanwhile.
- A store or model outage never fails the pipeline: judging is skipped and counted.
- Judgements are saved only for concerts the sync stored (foreign key to `concerts`).
- A new rating or an edit of the written taste changes the input of many concerts: they are
  re-judged on the next run, within the call cap.

## Cost impact
At €0.28 per 1,000 judgements (ADR-0006, run 4 measured tokens): a full window (~500 concerts)
≈ €0.14; a call cap of 600 per run bounds a day at ≈ €0.17, so ≤ ≈ €5 / month in the worst
case (every concert re-judged daily); expected much less (only changed inputs). Database and
function: within ADR-0005's free tiers (**unverified** for the extra reads; measured after a week).
