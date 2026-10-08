-- What Trainlog uses for one station of quai (the station index built from OSM) where OSM's
-- reads badly or is wrong: a name, a position, and tracks merged over OSM's by track
-- ([{ref, lat, lng, on_track}] adds a track or moves OSM's of that ref, [{ref, hidden: true}]
-- removes it). Keyed by the station's mode and quai station_key; applied wherever Trainlog
-- uses quai's results (src/quai.py), and edited from the admin station explorer.
CREATE TABLE IF NOT EXISTS station_overrides (
    mode        TEXT NOT NULL,
    station_key TEXT NOT NULL,
    name        TEXT,
    lat         DOUBLE PRECISION,
    lng         DOUBLE PRECISION,
    tracks      JSONB,
    updated_by  TEXT,
    updated_on  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (mode, station_key),
    CONSTRAINT station_overrides_position_check CHECK ((lat IS NULL) = (lng IS NULL))
);
