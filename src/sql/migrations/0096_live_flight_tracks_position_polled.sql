-- Between two refreshes of a live flight's whole track (FR24 flight-tracks, every few
-- minutes), its latest position alone is asked for more often (live flight-positions,
-- light) and appended to the stored trail: when that was last done, claimed like
-- last_polled so that one request polls it however many viewers there are.
ALTER TABLE live_flight_tracks
    ADD COLUMN IF NOT EXISTS position_polled timestamptz NOT NULL DEFAULT to_timestamp(0);
