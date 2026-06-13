-- Like get_unique_user_trips, but returns the representative trip's FULL column
-- set (f.*) instead of just the map-display subset. Used by the getUpdatedTrips
-- endpoint, which needs every modified-trip field (not only uid/type/dates) while
-- keeping the same route-deduplication, path geometry and lastLocal filtering as
-- get_unique_user_trips. Rows are passed through adapt_pg_trip_row in Python to get
-- legacy names and the 1/-1 date sentinels.
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
),
grouped AS (
    -- One representative trip (highest trip_id) per distinct route/year/category,
    -- plus how many trips collapsed into it.
    SELECT MAX(trip_id) AS rep_uid,
           count(*) AS count
    FROM filtered
    GROUP BY origin_station, destination_station, trip_length, trip_year,
             past, current, planned_future, future
)
SELECT
    -- Full representative-trip row: every trips column plus the computed
    -- utc_filtered_* / trip_year / past / current / planned_future / future fields.
    f.*,
    -- plannedFuture alias matches the legacy/frontend trip shape.
    f.planned_future AS "plannedFuture",
    g.count,
    -- Path geometry fetched in the same query. ST_AsGeoJSON emits (lng, lat);
    -- geom_geojson_to_coords swaps back to [lat,lng]. Full resolution is kept on
    -- purpose: zoomed-in path precision is a core feature.
    ST_AsGeoJSON(p.geom) AS geojson
FROM grouped g
JOIN filtered f ON f.trip_id = g.rep_uid
LEFT JOIN paths p ON p.trip_id = f.trip_id
ORDER BY f.utc_filtered_start_datetime DESC NULLS LAST
