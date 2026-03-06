-- Get countries visited by each user for the leaderboard
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

SELECT user_id, countries 
FROM counted 
WHERE user_id = ANY(:user_ids)
    AND past = 1
