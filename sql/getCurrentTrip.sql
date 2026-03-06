WITH UTC_Filtered AS (
    SELECT *, 
        COALESCE(
            datetime(
                utc_start_datetime,
                printf('%+d seconds', COALESCE(departure_delay, 0))
            ),
            start_datetime
        ) AS actual_start_datetime,
        COALESCE(
            datetime(
                utc_end_datetime,
                printf('%+d seconds', COALESCE(arrival_delay, 0))
            ),
            end_datetime
        ) AS actual_end_datetime
    FROM trip
)

SELECT uid
FROM UTC_Filtered 
WHERE username == :username
AND julianday('now') BETWEEN julianday(actual_start_datetime) AND julianday(actual_end_datetime)
