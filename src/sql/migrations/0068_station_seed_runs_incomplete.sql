-- A verdict reached on partial Photon data (one language timed out, another answered) is not
-- recorded as a checked "no" — see station_seed.py's use of candidate["incomplete"] — so it
-- stays in the queue for the next run instead of counting as skipped. Tracked separately here
-- so the admin panel can tell "genuinely unmatched" apart from "ask again later".
ALTER TABLE station_seed_runs ADD COLUMN IF NOT EXISTS incomplete INTEGER NOT NULL DEFAULT 0;
