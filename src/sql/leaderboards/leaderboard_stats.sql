-- Get leaderboard statistics grouped by user and trip trip_type
WITH utc_filtered AS (
    SELECT *,
        COALESCE(
            utc_start_datetime,
            start_datetime
        ) AS utc_filtered_start_datetime,
        COALESCE(
            utc_start_datetime + COALESCE(departure_delay, 0) * INTERVAL '1 second',
            start_datetime
        ) AS actual_start_datetime
    FROM trips
),
counted AS (
    SELECT *, 
        CASE
            WHEN (NOW() > actual_start_datetime 
                OR utc_filtered_start_datetime IS NULL)
                AND NOT is_project
            THEN 1
            ELSE 0
        END AS past,
        CASE
            WHEN NOW() <= actual_start_datetime
                AND NOT is_project
            THEN 1
            ELSE 0 
        END AS planned_future,
        CASE
            WHEN is_project
            THEN 1
            ELSE 0 
        END AS future
    FROM utc_filtered
)

SELECT 
    user_id, 
    trip_type,
    COUNT(*) AS trips,
    SUM(trip_length) AS length,
    MAX(last_modified) AS last_modified
FROM counted
WHERE future = 0
    AND planned_future = 0
GROUP BY user_id, trip_type

UNION ALL

SELECT 
    user_id, 
    'all' AS trip_type,
    COUNT(*) AS trips,
    SUM(trip_length) AS length,
    MAX(last_modified) AS last_modified
FROM counted
WHERE future = 0
    AND planned_future = 0
GROUP BY user_id
