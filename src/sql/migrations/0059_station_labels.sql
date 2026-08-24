-- Resolution cache: written station label -> canonical station. `trips.origin_station` stays
-- the source of truth; this holds what each distinct *spelling* resolves to.
--
-- Keyed on the label, not the trip. The first version followed trip_operators (0043) and had
-- one row per trip endpoint — right for operators, whose comma-separated text needs each
-- trip's own value, wrong here. Keyed on the trip it cost 1,490,740 rows to say 127,628
-- distinct things and needed a re-sync on every trip edit; keyed on the label a trip edit
-- needs no sync at all.
--
-- Reads join through the normalised form:
--
--     LEFT JOIN station_labels sl
--       ON sl.normalized   = station_normalize(t.origin_station)
--      AND sl.station_type = station_type_bucket(t.trip_type)
--     LEFT JOIN stations s ON s.station_id = sl.station_id

CREATE TABLE station_labels (
    label_id     SERIAL PRIMARY KEY,

    -- The comparison key: station_normalize() folds case, punctuation, accents and the flag,
    -- so "🇫🇷 Paris - Gare de Lyon" and "Paris Gare de Lyon" are one row.
    normalized   TEXT NOT NULL,

    -- A label resolves only against stations of its own mode, so the same spelling can be one
    -- row per mode, resolving differently in each.
    station_type TEXT NOT NULL,

    -- A readable example of this spelling for the admin queue. Not a key.
    sample_label TEXT NOT NULL,

    -- NULL when the spelling matches no station, or several with nothing to choose between
    -- them. That is the admin work queue, not a failure.
    station_id   INTEGER REFERENCES stations (station_id) ON DELETE SET NULL,

    -- Refreshed in bulk by refresh_label_counts(), not maintained per write: these only order
    -- the admin queue, and an hour-stale count orders it as well as a live one.
    occurrences  INTEGER NOT NULL DEFAULT 0,
    users        INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT station_labels_normalized_check CHECK (normalized <> '')
);

-- The join key, and the uniqueness that makes this a cache rather than a log.
CREATE UNIQUE INDEX station_labels_key ON station_labels (station_type, normalized);
CREATE INDEX station_labels_station_id_idx ON station_labels (station_id);
-- The admin queue: unresolved spellings, costliest first.
CREATE INDEX station_labels_unresolved_idx ON station_labels (occurrences DESC)
    WHERE station_id IS NULL;

-- So a trip can be found by what its label normalises to, without scanning.
CREATE INDEX trips_origin_station_normalized_idx
    ON trips (station_normalize(origin_station));
CREATE INDEX trips_destination_station_normalized_idx
    ON trips (station_normalize(destination_station));

-- Seed one row per distinct spelling in use. Unresolved for now: stations are registered
-- lazily, so there is nothing yet for these to point at. Air is excluded — see
-- REGISTRY_EXCLUDED_TYPES in src/stations.py, which this must stay in step with.
INSERT INTO station_labels (normalized, station_type, sample_label, occurrences, users)
SELECT normalized,
       station_type,
       -- The most-used spelling of the group reads best in the queue.
       (array_agg(raw ORDER BY n DESC))[1],
       sum(n)::int,
       max(u)::int
FROM (
    SELECT station_normalize(origin_station) AS normalized,
           station_type_bucket(trip_type)    AS station_type,
           origin_station                    AS raw,
           count(*)                          AS n,
           count(DISTINCT user_id)           AS u
    FROM trips
    WHERE station_normalize(origin_station) IS NOT NULL
      AND station_type_bucket(trip_type) <> 'air'
    GROUP BY 1, 2, 3
    UNION ALL
    SELECT station_normalize(destination_station),
           station_type_bucket(trip_type),
           destination_station,
           count(*),
           count(DISTINCT user_id)
    FROM trips
    WHERE station_normalize(destination_station) IS NOT NULL
      AND station_type_bucket(trip_type) <> 'air'
    GROUP BY 1, 2, 3
) spellings
GROUP BY normalized, station_type;

ANALYZE station_labels;
