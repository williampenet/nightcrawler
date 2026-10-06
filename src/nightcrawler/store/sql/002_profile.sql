-- WIP-46: the listener's taste profile (seed artists and ratings), one row, synced by the page
-- through the feedback function. No Spotify token here (ADR-0003): only the resulting names.
CREATE TABLE profile (
    id          TEXT PRIMARY KEY DEFAULT 'me',
    data        JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    version     BIGINT NOT NULL DEFAULT 1         -- optimistic concurrency (PUT base_version)
);

-- one row per accepted PUT, for the per-minute rate limit; rows older than an hour are pruned
CREATE TABLE profile_writes (
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX profile_writes_at ON profile_writes (at);
