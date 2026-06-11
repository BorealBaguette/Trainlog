-- "Plans": a parallel planning world. A plan is a self-contained itinerary of
-- trip-like rows that NEVER appears in stats/leaderboards/the global map/the trips
-- list — hence separate tables (not a flag on `trips`). plan_trips carries its path
-- geometry INLINE (the shared `paths` table is keyed by trip_id, which would
-- collide), reusing coords_to_ewkt / geom_geojson_to_coords (src/paths.py).

CREATE TABLE plans (
    uid             SERIAL PRIMARY KEY,
    uuid            TEXT UNIQUE NOT NULL,           -- shareable id (/public/plan/<uuid>)
    user_id         INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    -- Day-1 reference used to materialise a timezone-correct preview of relative
    -- (undated) trips. Editable; the real anchor is chosen again at validation.
    anchor_date     DATE NOT NULL DEFAULT CURRENT_DATE,
    archived        BOOLEAN NOT NULL DEFAULT FALSE,
    created         TIMESTAMP,
    last_modified   TIMESTAMP
);
CREATE INDEX plans_user_id_idx ON plans (user_id);

CREATE TABLE plan_trips (
    uid                       SERIAL PRIMARY KEY,
    plan_id                   INTEGER NOT NULL REFERENCES plans(uid) ON DELETE CASCADE,
    user_id                   INTEGER NOT NULL,
    sort_order                INTEGER NOT NULL DEFAULT 0,

    -- Timing. timing_mode discriminates how the row is timed:
    --   'relative'  -> start_day/end_day (1-based: Day 1 = plans.anchor_date) + times.
    --                  start/end_datetime + utc_* are MATERIALISED for the preview.
    --   'precise' | 'onlyDate' | 'unknown' -> mirror the trip builder's precision;
    --                  start/end_datetime (+ utc_*) carry the usual values/sentinels.
    timing_mode               TEXT NOT NULL DEFAULT 'relative',
    start_day                 INTEGER,
    end_day                   INTEGER,
    start_time                TIME,
    end_time                  TIME,
    start_datetime            TIMESTAMP,
    end_datetime              TIMESTAMP,
    utc_start_datetime        TIMESTAMP,
    utc_end_datetime          TIMESTAMP,
    estimated_trip_duration   INTEGER,
    manual_trip_duration      INTEGER,

    -- Rendering subset of `trips` (enough for the itinerary+map view and to build a
    -- real Trip at validation). Other trip columns are added later if needed.
    origin_station            TEXT,
    destination_station       TEXT,
    trip_type                 TEXT,
    operator                  TEXT,
    line_name                 TEXT,
    material_type             TEXT,
    material_type_advanced    TEXT,
    reg                       TEXT,
    seat                      TEXT,
    notes                     TEXT,
    trip_length               DOUBLE PRECISION,
    countries                 TEXT,
    price                     DOUBLE PRECISION,
    currency                  TEXT,
    purchase_date             TIMESTAMP,
    waypoints                 TEXT,
    visibility                TEXT,
    carbon                    DOUBLE PRECISION,
    power_type                TEXT,                       -- traction (drives the electrified split)
    co2_override              DOUBLE PRECISION,           -- custom CO2 emission factor

    geom                      geometry(Geometry, 4326),   -- inline path; dies with the row

    created                   TIMESTAMP,
    last_modified             TIMESTAMP
);
CREATE INDEX plan_trips_plan_id_idx ON plan_trips (plan_id);
CREATE INDEX plan_trips_geom_gix ON plan_trips USING GIST (geom);
