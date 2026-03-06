WITH UTC_Filtered AS (
    SELECT *, 
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime,
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

SELECT 
    t.*,
    CASE
        WHEN julianday('now') > julianday(actual_end_datetime) 
            OR utc_filtered_start_datetime = -1
            AND utc_filtered_start_datetime != 1
        THEN 'past'
        WHEN julianday('now') <= julianday(actual_start_datetime)
        THEN 'plannedFuture'
        WHEN julianday('now') BETWEEN  julianday(actual_start_datetime) AND julianday(actual_end_datetime)
        THEN 'current'
        WHEN utc_filtered_start_datetime = 1
        THEN 'future'
    END AS 'time',
    o.short_name AS operator_name,
    CASE
        -- Fetch the oldest logo if trip date is -1
        WHEN utc_filtered_start_datetime = -1 THEN (
            SELECT l.logo_url
            FROM operator_logos l
            WHERE l.operator_id = o.uid
            ORDER BY l.effective_date ASC
            LIMIT 1
        )
        -- Fetch the latest logo if trip date is 1
        WHEN utc_filtered_start_datetime = 1 THEN (
            SELECT l.logo_url
            FROM operator_logos l
            WHERE l.operator_id = o.uid
            ORDER BY l.effective_date DESC
            LIMIT 1
        )
        -- Fetch the logo closest to the trip start date
        ELSE (
            SELECT l.logo_url
            FROM operator_logos l
            WHERE l.operator_id = o.uid
              AND (l.effective_date <= t.utc_filtered_start_datetime OR l.effective_date IS NULL)
            ORDER BY l.effective_date DESC
            LIMIT 1
        )
    END AS logo_url
FROM UTC_Filtered t
LEFT JOIN operators o ON t.operator = o.short_name
WHERE t.uid = :trip_id;
