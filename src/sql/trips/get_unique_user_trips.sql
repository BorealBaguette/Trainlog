-- Unique routes for a user's map (one representative trip per distinct
-- route + year + temporal category).
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
)
SELECT
    MAX(trip_id) AS uid,
    origin_station,
    destination_station,
    trip_length,
    trip_year,
    past,
    current,
    planned_future AS "plannedFuture",
    future,
    count(*) AS count
FROM flagged
WHERE (:lastLocal = 'all' OR last_modified > CAST(NULLIF(:lastLocal, 'all') AS timestamp))
  AND (
      :public = 0
      OR (:public = 1 AND visibility = 'public')
      OR (:friend = 1 AND visibility = 'friends')
      OR (visibility IS NULL AND trip_type IN ('train', 'air', 'bus', 'ferry', 'aerialway', 'tram', 'metro'))
  )
GROUP BY origin_station, destination_station, trip_length, trip_year,
         past, current, planned_future, future
ORDER BY MAX(utc_filtered_start_datetime) DESC NULLS LAST
