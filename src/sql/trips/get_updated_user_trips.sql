-- Full per-trip data for every trip modified since :lastLocal, used by the
-- getUpdatedTrips endpoint to update a local DB. Unlike get_unique_user_trips
-- there is NO route deduplication: each trip is returned individually (one row
-- per trip_id) with its FULL column set (f.*), so callers get complete,
-- per-UID modified-trip data. Rows are passed through adapt_pg_trip_row in
-- Python to get legacy names and the 1/-1 date sentinels.
WITH base AS (
    SELECT trips.*,
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime
    FROM trips
    WHERE user_id = :user_id
),
flagged AS (
    SELECT *,
        to_char(utc_filtered_start_datetime, 'YYYY') AS trip_year,
        CASE WHEN NOT is_project
                  AND (utc_filtered_end_datetime IS NULL OR NOW() > utc_filtered_end_datetime)
             THEN 1 ELSE 0 END AS past,
        CASE WHEN NOW() BETWEEN utc_filtered_start_datetime AND utc_filtered_end_datetime
             THEN 1 ELSE 0 END AS current,
        CASE WHEN utc_filtered_start_datetime IS NOT NULL AND NOW() <= utc_filtered_start_datetime
             THEN 1 ELSE 0 END AS planned_future,
        CASE WHEN utc_filtered_start_datetime IS NULL AND is_project
             THEN 1 ELSE 0 END AS future
    FROM base
),
filtered AS (
    SELECT * FROM flagged
    WHERE (:lastLocal = 'all' OR last_modified > CAST(NULLIF(:lastLocal, 'all') AS timestamp))
      AND (
          :public = 0
          OR (:public = 1 AND visibility = 'public')
          OR (:friend = 1 AND visibility = 'friends')
          OR (visibility IS NULL AND trip_type IN ('train', 'air', 'bus', 'ferry', 'aerialway', 'tram', 'metro'))
      )
)
SELECT
    -- Full trip row: every trips column plus the computed
    -- utc_filtered_* / trip_year / past / current / planned_future / future fields.
    f.*,
    -- plannedFuture alias matches the legacy/frontend trip shape.
    f.planned_future AS "plannedFuture",
    -- Path geometry fetched in the same query. ST_AsGeoJSON emits (lng, lat);
    -- geom_geojson_to_coords swaps back to [lat,lng]. Full resolution is kept on
    -- purpose: zoomed-in path precision is a core feature.
    ST_AsGeoJSON(p.geom) AS geojson
FROM filtered f
LEFT JOIN paths p ON p.trip_id = f.trip_id
ORDER BY f.utc_filtered_start_datetime DESC NULLS LAST
