# Reference set (PRD FR-11)

`watch_events.csv` lists the concerts William's former weekly concert watch reported
(its memory file `concerts-vus.md`, shared by William on 2026-10-06). The rows are copied
verbatim (`date | artists | venue` → `date,artists,venue`); the file's other text (its
header and run headings) is left out. These are public events only.

Count: the shared file holds 69 event rows (2026-07-26 → 2026-12-17), counted with
`grep -c '^20'` on it. The PRD and the build log say 76: that number is not what the file
contains.

What it is for:

- **Source coverage** (FR-1 gate): each Pipeline run matches the reference events of its
  window against its concerts (`src/nightcrawler/coverage.py`) and reports
  `coverage = {in_window, found, rate, per_venue}` in `data/report.json`, the run
  annotation and the job summary. Method: same local date; same venue (normalised name, or
  an alias from `venue_aliases.yaml`); at least one artist of the row (split on `+ , & /`)
  found as whole words in the concert's title or performers. A venue-only match does not
  count.
- **Judgement quality** (FR-5): these events are the positives; negatives come from the
  in-app ratings, read from the event store at eval time and never committed.
