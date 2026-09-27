-- A shared allowance for the third-party APIs we are a guest of.
--
-- Overpass and Wikidata are public and rate-limit us; this project has already been banned
-- from one free geocoder for volume. Counting in the process does not work: prod runs four
-- gunicorn workers, each with its own memory, plus a background enricher thread in every one
-- of them. The database is the only thing they all share, so the allowance lives here.
CREATE TABLE IF NOT EXISTS external_api_budget (
    service    TEXT PRIMARY KEY,
    -- Token bucket: refilled by elapsed time, spent one per call. The row lock is what makes
    -- concurrent workers queue rather than each read the same balance.
    tokens     DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
