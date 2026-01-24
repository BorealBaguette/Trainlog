WITH UTC_Filtered AS (
    SELECT t.*,
    COALESCE(t.utc_start_datetime, t.start_datetime) AS utc_filtered_start_datetime,
    COALESCE(t.utc_end_datetime, t.end_datetime) AS utc_filtered_end_datetime
    FROM trips t
)

SELECT trip_id
FROM UTC_Filtered
WHERE user_id = :user_id
AND NOW() BETWEEN utc_filtered_start_datetime AND utc_filtered_end_datetime
