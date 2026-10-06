-- ADR-0005: event store. Applied once, in order, by nightcrawler.store.migrate.

-- every event as a source published it, kept across runs
CREATE TABLE raw_events (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,            -- "ticketmaster", "gancio:agenda.villemorte.fr", "site:..."
    source_key  TEXT NOT NULL,            -- the source's own id, else its URL + start
    payload     JSONB NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_key)
);

-- one canonical record per real concert; its id never changes once published
CREATE TABLE concerts (
    id          TEXT PRIMARY KEY,
    aliases     TEXT[] NOT NULL DEFAULT '{}',
    data        JSONB NOT NULL,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- which raw events make up which concert, and why (de-dup rule that matched)
CREATE TABLE concert_sources (
    concert_id  TEXT NOT NULL REFERENCES concerts (id) ON DELETE CASCADE,
    raw_id      BIGINT NOT NULL REFERENCES raw_events (id) ON DELETE CASCADE,
    rule        TEXT NOT NULL,
    PRIMARY KEY (concert_id, raw_id)
);

CREATE INDEX concert_sources_raw ON concert_sources (raw_id);

-- manual corrections that the de-dup rules must respect
CREATE TABLE overrides (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('merge', 'split', 'not_concert')),
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- the listener's ratings, sent by the page through the feedback function.
-- No foreign key on concert_id: a rating can arrive before or after the concert is stored.
CREATE TABLE feedback (
    id          BIGSERIAL PRIMARY KEY,
    concert_id  TEXT,
    artist_key  TEXT,
    kind        TEXT NOT NULL CHECK (kind IN ('like', 'unlike', 'dislike', 'wrong')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (concert_id IS NOT NULL OR artist_key IS NOT NULL)
);
CREATE INDEX feedback_artist ON feedback (artist_key);
