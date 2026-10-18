-- Stations a user chose to leave with the names they have on the tidy stations page; labels
-- are those names, so that a name given since brings the station back to tidy.
CREATE TABLE IF NOT EXISTS station_cleanup_ignored (
    user_id     INTEGER NOT NULL,
    mode        TEXT NOT NULL,
    station_key TEXT NOT NULL,
    labels      TEXT[] NOT NULL,
    PRIMARY KEY (user_id, mode, station_key)
);
