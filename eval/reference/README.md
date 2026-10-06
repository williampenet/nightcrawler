# Reference set (PRD FR-11)

`watch_events.csv` lists the concerts William's former weekly concert watch reported
(its memory file `concerts-vus.md`, shared by William on 2026-10-06). The rows are copied
verbatim (`date | artists | venue` → `date,artists,venue`); the file's other text (its
header and run headings) is left out.

Count: the shared file holds 69 event rows (2026-07-26 → 2026-12-17), counted with
`grep -c '^20'` on it. One is left out here: the 2026-08-01 row held at a private place
(its venue column names a private location, not a public venue), so the CSV has 68 rows,
all public events. It was outside every coverage window measured since 2026-10-06.

What it is for:

- **Source coverage** (FR-1 gate): each Pipeline run matches the reference rows of its
  window against its concerts (`src/nightcrawler/coverage.py`) and reports
  `coverage = {in_window, found, rate, date_venue_only, per_venue, events}` in
  `data/report.json`; the run annotation and the job summary give the totals. Method:
  same local date; same venue (normalised name, or an alias); at least one artist of the
  row (split on `+ , & /`, leading article and generic words such as "trio" dropped) found
  as whole words in the concert's title or performers. A venue-only match does not count;
  `date_venue_only` counts those rows as a diagnostic. Window: rows from today up to, not
  including, the date of now + `window_days` (the run's last day is only partly covered).
  Venue aliases and the dropped words are in `matching.yaml`.
- **Judgement quality** (FR-5): these events are the positives; negatives come from the
  in-app ratings, read from the event store at eval time and never committed.
