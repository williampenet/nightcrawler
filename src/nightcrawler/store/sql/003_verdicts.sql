-- ADR-0007 (WIP-83): taste judgements of the routed judge_taste model (ADR-0006), one per
-- concert. Personal data (they describe the listener's taste): written by the pipeline, read by
-- the feedback function with the send key, never published in the site data.
CREATE TABLE verdicts (
    concert_id  TEXT PRIMARY KEY REFERENCES concerts (id) ON DELETE CASCADE,
    input_hash  TEXT NOT NULL,                -- judge.input_hash: unchanged input, no new call
    verdict     TEXT NOT NULL CHECK (verdict IN ('must_see', 'for_you', 'discovery', 'no')),
    confidence  SMALLINT NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    reason      TEXT NOT NULL CHECK (char_length(reason) <= 240),
    section     TEXT NOT NULL
                CHECK (section IN ('ne_pas_rater', 'pour_toi', 'decouvertes', 'tout_voir')),
    model       TEXT NOT NULL,
    starts_at   TIMESTAMPTZ NOT NULL,             -- the concert's start: GET /verdicts window, purge
    judged_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- the section follows the verdict (judge.section); a discovery under the confidence bar
    -- stays in "tout_voir"
    CHECK ((verdict = 'must_see' AND section = 'ne_pas_rater')
        OR (verdict = 'for_you' AND section = 'pour_toi')
        OR (verdict = 'discovery' AND section IN ('decouvertes', 'tout_voir'))
        OR (verdict = 'no' AND section = 'tout_voir'))
);
CREATE INDEX verdicts_starts_at ON verdicts (starts_at);
