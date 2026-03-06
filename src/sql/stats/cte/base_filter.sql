-- Base filtering CTE - filters trips by type, user, and year
SELECT *,
    COALESCE(utc_start_datetime, start_datetime) AS filtered_datetime,
    COALESCE(
        utc_start_datetime + COALESCE(departure_delay, 0) * INTERVAL '1 second',
        start_datetime
    ) AS actual_datetime
FROM trips
WHERE trip_type = :tripType
AND (:user_id IS NULL OR user_id = :user_id)
AND (:year IS NULL OR EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime))::text = :year)
