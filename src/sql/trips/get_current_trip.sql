WITH utc_filtered AS (
    SELECT *,
        COALESCE(
            utc_start_datetime + COALESCE(departure_delay, 0) * INTERVAL '1 second',
            start_datetime
        ) AS actual_start_datetime,
        COALESCE(
            utc_end_datetime + COALESCE(arrival_delay, 0) * INTERVAL '1 second',
            end_datetime
        ) AS actual_end_datetime
    FROM trips
)

SELECT trip_id
FROM utc_filtered
WHERE user_id = :user_id
AND NOW() BETWEEN actual_start_datetime AND actual_end_datetime
