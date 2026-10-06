# Taste eval (WIP-52)

Question: does the personal score (`src/nightcrawler/web/scoring.js`) put first the concerts
the listener is sure to like? This eval measures it on the listener's own ratings, with no
leakage of a rating into its own score.

- Runner: `eval/taste/run.js` (Node). It `require`s `scoring.js` and never reimplements
  scoring. Input on stdin: `{state, feedback, concerts, artists}`; output: aggregated JSON.
- Loader: `eval/taste/load.py`. Reads the store, the published site data, runs the runner and
  publishes the aggregates.
- Workflow: `.github/workflows/taste.yml` (**Taste eval**). Runs after each successful
  **Pipeline** run, and on demand.
- Tests: `tests/js/taste.test.js` (runner, synthetic profile) and `tests/test_taste_eval.py`
  (loader, including a run on a local PostgreSQL when `TEST_DATABASE_URL` points to one).

## Data

| What | Where | Why |
|---|---|---|
| Profile (`state`) | `profile.data` where `id = 'me'` (`002_profile.sql`) | The browser state synced by the page (shape: `profile.js` `extract()`, validated by `functions/feedback/handler.py` `validate_profile()`). |
| Rating history (`feedback`) | `feedback` rows with a `concert_id` and kind `like` / `unlike` / `dislike`, ordered by `created_at, id` | The page sends one item per click with the concert id (`feedback.js` `makeItem()`). The function stores **one row per artist key** of the item (one row with a null `artist_key` for a concert without an identified artist: `handler.py` `validate()`); rows of one click carry the same kind, so they count as one event per concert. One request is one transaction, so its rows share `created_at` and `id` keeps their order. |
| Concerts, artists | Published site: `data/concerts.json`, `data/artists.json` (GitHub Pages) | Exactly what the page scores. The store's `concerts.data` is not usable: it is written by `store/sync.py` (`kept.to_dict()`) **before** artist enrichment (`pipeline.py`: `sync.sync(...)` then `enrich(...)`, which sets `c.artists` in `artists.py`), so its `artists` lists are empty, and artists are not stored at all. |

The loader opens one connection (the store's `CONNECT` settings), runs both reads in one
`READ ONLY` transaction with `statement_timeout = 30s`, and closes it. The database URL
comes from `DATABASE_URL`, else from the `SCW_*` secrets like `run --store`
(`provision.ensure(create=False)`: looked up, never created).

The site is fetched after the Pipeline's deploy job succeeded, with a `?v=<timestamp>` query
to ask the CDN for a fresh copy. Unverified: whether GitHub Pages' CDN can still serve the
previous copy for a few minutes; the summary prints the data's `generated_at` so a stale
read is visible.

## Labels

A label is "liked" or "disliked" on a concert of the current site data. Saved ids are first
mapped to current ids through `aliases` (`currentIds()`, as the page does on load).

1. **Profile, concert-level evidence only**
   - liked: the concert id is in `likedConcerts` (and not in `hidden`);
   - disliked: the id is in `hidden`, and every artist key of the concert is in `disliked`
     (or, without an identified artist, every performer name is in `dislikedNames`). A
     hidden concert whose artist was liked again later is not a dislike any more.
   - Not a label: `isLiked()` through artist keys alone. Liking one concert of an artist
     lights "J'aime" on all of that artist's concerts; counting them would score the same
     click several times, against itself.
2. **Rating history: the latest event per concert wins**, if the profile still shows it.
   `like` → liked, only while `isLiked(state, concert)`; `dislike` → disliked, only while
   the concert id is in `hidden`; `unlike` → no label (even if the profile still holds it,
   e.g. after a multi-device merge brought a removed like back, a known limit of
   `profile.js merge()`). `wrong` events rate the artist match, not the taste: ignored.
   - A like or dislike the profile no longer shows is **stale** (`stale_feedback`), no
     label. It happens when the rating was undone without an event on that concert: a
     click on a sibling concert of the same artist (its lit "J'aime" sends an `unlike`
     for the sibling and removes the artist from `liked`; a dislike of the sibling moves
     the artist to `disliked`), or « Tout effacer », which empties the profile but not the
     history.
   - A dislike that shares an artist key or performer name with a stale like is
     **ambiguous** (`ambiguous_dislikes`), no label. Either it undid that like (dislike
     through a sibling: without it, the like would still count and the leave-one-out
     score would be higher) or a reset did (then it would not); the history does not
     record resets, so the two cannot be told apart.

The summary reports how many labels come from the history and how many from the profile
only, how many ratings are stale or ambiguous, and how many rating events point to
concerts no longer published (past concerts).

## Leakage handling (leave-one-out)

For each labelled concert, the profile is reduced before scoring. What a rating
**produces** mirrors `rate()`:

- like: its artist keys in `liked`; without an identified artist, its id in
  `likedConcerts` and its performer names in `likedNames`;
- dislike: its artist keys in `disliked`; without an identified artist, its performer names
  in `dislikedNames`; in both cases its id in `hidden`.

What is **removed** is broader, so values saved by older versions of the page (e.g. a name
liked before its artist was identified) go too: for a like, its id from `likedConcerts`,
its artist keys from `liked`, its performer names and artist keys from `likedNames`; for a
dislike, its id from `hidden`, its artist keys from `disliked`, its performer names and
artist keys from `dislikedNames`.

A removed value is kept when another labelled concert produced it in the same list (two
disliked concerts of the same artist: each one keeps the artist in `disliked` for the
other; a liked concert without an artist keeps its name in `likedNames` for a liked
concert whose artist has that name, not the other way round). A value produced by a
concert that is not labelled (a past concert) is removed with the label: conservative, no
leakage. Two likes on concerts of the same artist cannot both be labels: the second click
on a lit "J'aime" is an `unlike`.

Tests (`tests/js/taste.test.js`, "replay") check this against a ground truth: they build
the profile and the history by replaying clicks with `rate()` (concerts with and without
artists, an unlike through a sibling, a dislike through a sibling, a reset), and for each
label compare the leave-one-out score with the score after replaying every click except
the ones on that concert.

Then `scoreConcert(concert, artists, buildProfile(reduced, artists))`. Seeds (artist names
typed or imported from Spotify, with their styles) are kept: they are inputs, not labels.

## Metrics

**Tiers** (by leave-one-out score), each with `n`, `liked`, `disliked` and
precision = liked / (liked + disliked):

| Tier | Score | Meaning on the page |
|---|---|---|
| sure | ≥ 0.9 | "Tu écoutes …" (1) or "Tu as aimé …" (0.9) |
| inferred | 0 < score < 0.9 | "Proche de …" (0.8 / 0.7) or "Style : …" (≤ 0.6) |
| none | 0 | no match |

**Wilson 95 % interval** for each precision (Wilson, 1927,
[doi:10.1080/01621459.1927.10502953](https://doi.org/10.1080/01621459.1927.10502953);
formula as given in
[Binomial proportion confidence interval, Wilson score interval](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval#Wilson_score_interval)),
with p = liked / n and z = 1.96:

    centre = (p + z²/2n) / (1 + z²/n)
    half   = z / (1 + z²/n) · √(p(1−p)/n + z²/4n²)

Checked in the tests: 5/10 → [0.237, 0.763].

**Pairwise ranking accuracy**: over every (liked, disliked) pair, 1 when the liked concert
scores higher, 0.5 on a tie, 0 otherwise, averaged. It is the probability that a random
liked concert is ranked above a random disliked one, i.e. the ROC AUC (Hanley & McNeil, 1982,
[doi:10.1148/radiology.143.1.7063747](https://doi.org/10.1148/radiology.143.1.7063747)).
0.5 = no better than chance.

## How to read the numbers

- **sure precision** answers the ticket's question: of the concerts the page would put first
  with "Tu écoutes / Tu as aimé", how many the listener actually likes. The target is close
  to 100 %; a disliked concert in this tier is a false certainty worth looking at.
- **inferred precision** tells whether "Proche de" / "Style" guesses are better than the
  base rate (overall liked share); **none** shows what the score misses (liked concerts at 0).
- **pairwise accuracy** summarises the whole ranking in one number; with many ties at 0 it
  sits near 0.5 even when the sure tier is right.
- Always read a rate with its `n` and its interval.

## Limits

- **Few labels, wide uncertainty.** With n = 5 and 5 liked, the Wilson interval is
  [0.566, 1]: a 100 % precision on a handful of labels proves little.
- **Not a random sample.** The listener rates what the page shows, sorted by this very
  score; ratings say nothing about concerts never looked at.
- **Current concerts only.** Ratings of past concerts are not labels (their concerts are no
  longer in the site data), but what they put in the profile still feeds the scores.
- **Ids.** A label is lost when a concert's id and aliases both changed between runs (the
  store keeps ids stable, ADR-0005).
- **Time.** `created_at` is the server's receipt time: ratings queued offline arrive later,
  in their original order.

## Privacy

The repository is public, so annotations and job summaries are public. The eval publishes
only counts and rates (one `::notice` annotation and `$GITHUB_STEP_SUMMARY`), never an
artist name, title, concert id, the profile or the connection details; the URL and, separately, its host, user and password are masked, the `httpx` request log is off,
errors print the exception type only, and nothing is committed. Tests check that the output
contains none of the synthetic names and ids.

## Run locally

```bash
DATABASE_URL=postgresql://… python -m eval.taste.load --site site   # or --site https://…/
node --test tests/js/*.test.js && python -m pytest -q tests/test_taste_eval.py
```
