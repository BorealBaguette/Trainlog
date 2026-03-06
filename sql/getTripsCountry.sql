WITH UTC_Filtered AS (
    SELECT *, 
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(
            datetime(
                utc_start_datetime,
                printf('%+d seconds', COALESCE(departure_delay, 0))
            ),
            start_datetime
        ) AS actual_start_datetime
    FROM trip
)

SELECT *,
CASE
	WHEN  
		(
			julianday('now') > julianday(actual_start_datetime) 
			OR utc_filtered_start_datetime = -1
		)
		AND utc_filtered_start_datetime != 1
	THEN 1
	ELSE 0
END AS 'past',
CASE
	WHEN julianday('now') <= julianday(actual_start_datetime)
	THEN 1
	ELSE 0 
END AS 'plannedFuture',
CASE
	WHEN utc_filtered_start_datetime=1
	THEN 1
	ELSE 0 
END AS 'future'

FROM UTC_Filtered 
WHERE username = :username
AND type IN ('train', 'tram', 'metro')
AND future = 0
AND plannedFuture = 0
AND (
    CASE 
        WHEN :country LIKE '%CN%' OR :country LIKE '%HK%' OR :country LIKE '%MO%' THEN 
            (countries LIKE '%CN%' OR countries LIKE '%HK%' OR countries LIKE '%MO%')
        ELSE 
            countries LIKE :country
    END
)
