-- Base filtering CTE - filters trips by type, user, and year
-- :year is a year, 'past' (the trips time_categories counts as past) or NULL.
SELECT *,
    COALESCE(utc_start_datetime, start_datetime) AS filtered_datetime
FROM trips
WHERE trip_type = :tripType
AND (:user_id IS NULL OR user_id = :user_id)
AND (
    :year IS NULL
    OR (:year = 'past' AND (COALESCE(utc_start_datetime, start_datetime) IS NULL
                            OR NOW() > COALESCE(utc_start_datetime, start_datetime)))
    OR EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime))::text = :year
)
