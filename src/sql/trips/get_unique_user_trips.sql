-- Unique routes for a user's map (one representative trip per distinct
-- route + year + temporal category). Returns the representative trip's full
-- datetime/type columns so the map frontend (new_map.html) can read
-- start_datetime/type and run computeTimeStatus; rows are passed through
-- adapt_pg_trip_row in Python to get legacy names and the 1/-1 date sentinels.
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
    f.trip_id AS uid,
    f.trip_type,
    f.is_project,
    f.origin_station,
    f.destination_station,
    f.trip_length,
    f.trip_year,
    f.start_datetime,
    f.end_datetime,
    f.utc_filtered_start_datetime,
    f.utc_filtered_end_datetime,
    f.past,
    f.current,
    f.planned_future AS "plannedFuture",
    f.future,
    g.count
FROM grouped g
JOIN filtered f ON f.trip_id = g.rep_uid
ORDER BY f.utc_filtered_start_datetime DESC NULLS LAST
