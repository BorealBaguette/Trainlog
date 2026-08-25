-- Progress of a seeding pass started from the admin panel. See src/station_seed.py.
--
-- The progress lives here rather than in the process because prod runs four gunicorn workers:
-- the thread doing the work is in one of them, and the browser's poll lands on whichever
-- answers first. `updated_at` is the heartbeat — a run whose worker was restarted mid-pass
-- stops touching it, which is how a dead run is told apart from a slow one.
CREATE TABLE IF NOT EXISTS station_seed_runs (
    run_id           SERIAL PRIMARY KEY,
    started_by       TEXT NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    params           JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- running | done | stopped | failed
    state            TEXT NOT NULL DEFAULT 'running',
    -- Set by the Stop button; the run reads it after each label and ends cleanly. Nothing is
    -- lost by stopping: a label is done when station_labels.auto_checked_at is set.
    stop_requested   BOOLEAN NOT NULL DEFAULT FALSE,
    total            INTEGER NOT NULL DEFAULT 0,
    attempted        INTEGER NOT NULL DEFAULT 0,
    registered       INTEGER NOT NULL DEFAULT 0,
    skipped          INTEGER NOT NULL DEFAULT 0,
    failed           INTEGER NOT NULL DEFAULT 0,
    endpoints_gained INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    -- The tail of what scrolled past on the console, most recent last.
    log              JSONB NOT NULL DEFAULT '[]'::jsonb
);
