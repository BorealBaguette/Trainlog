-- Trip list for the user's map (new_map.html), WITHOUT geometry: one
-- representative trip per distinct route + year + temporal category (same
-- grouping as get_unique_user_trips), plus the version of its path so the
-- client can tell which cached paths are stale. Geometry is fetched separately
-- and on demand through get_map_paths. Only the needed columns are carried:
-- `flagged` is read twice so it gets materialised, and trips.* spilled to disk.
-- Dates come out already formatted; NULLs become the 1/-1 sentinels in Python.
WITH base AS (
    SELECT trip_id, trip_type, is_project, origin_station, destination_station,
        trip_length, start_datetime,
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime
    FROM trips
    WHERE user_id = :user_id
      AND (
          :public = 0
          OR (:public = 1 AND visibility = 'public')
          OR (:friend = 1 AND visibility = 'friends')
          OR (visibility IS NULL AND trip_type IN ('train', 'air', 'bus', 'ferry', 'aerialway', 'tram', 'metro'))
      )
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
grouped AS (
    SELECT MAX(trip_id) AS rep_uid
    FROM flagged
    GROUP BY origin_station, destination_station, trip_length, trip_year,
             past, current, planned_future, future
)
SELECT
    f.trip_id AS uid,
    f.trip_type,
    f.is_project,
    f.origin_station,
    f.destination_station,
    -- same "YYYY-MM-DD HH:MM:SS" as _fmt_legacy_dt (YYYY is zero-padded)
    to_char(f.start_datetime, 'YYYY-MM-DD HH24:MI:SS') AS start_datetime,
    to_char(f.utc_filtered_start_datetime, 'YYYY-MM-DD HH24:MI:SS') AS utc_filtered_start_datetime,
    to_char(f.utc_filtered_end_datetime, 'YYYY-MM-DD HH24:MI:SS') AS utc_filtered_end_datetime,
    -- Milliseconds, so it survives JSON as an exact integer
    (EXTRACT(EPOCH FROM p.geom_updated) * 1000)::bigint AS path_version
FROM grouped g
JOIN flagged f ON f.trip_id = g.rep_uid
JOIN paths p ON p.trip_id = f.trip_id
ORDER BY f.utc_filtered_start_datetime DESC NULLS LAST, f.trip_id DESC
