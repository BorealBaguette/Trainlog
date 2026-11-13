-- Get countries visited by each user for the leaderboard
WITH utc_filtered AS (
    SELECT *,
        CASE
            WHEN utc_start_datetime IS NOT NULL
            THEN utc_start_datetime
            ELSE start_datetime 
        END AS utc_filtered_start_datetime
    FROM trips
),
counted AS (
    SELECT *, 
        CASE
            WHEN (NOW() > utc_filtered_start_datetime 
                OR utc_filtered_start_datetime IS NULL)
                AND NOT is_project
            THEN 1
            ELSE 0
        END AS past,
        CASE
            WHEN NOW() <= utc_filtered_start_datetime
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

SELECT user_id, countries, trip_type
FROM counted 
WHERE user_id = ANY(:user_ids)
    AND past = 1
