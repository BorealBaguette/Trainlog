WITH RECURSIVE

UTC_Filtered AS (
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


), counted AS (SELECT *, 
CASE
	WHEN  (julianday('now') > julianday(actual_start_datetime) 
	OR utc_filtered_start_datetime = -1)
	AND utc_filtered_start_datetime != 1
	THEN 1
	ELSE 0
END AS 'past',
CASE
	WHEN  julianday('now') <= julianday(actual_start_datetime)
	THEN 1
	ELSE 0 
END AS 'plannedFuture',
CASE
	WHEN  utc_filtered_start_datetime=1
	THEN 1
	ELSE 0 
END AS 'future'
from UTC_Filtered)


SELECT 
    username, 
    type,
    count(*) as 'trips',
    sum(trip_length) as 'length',
    max(last_modified) as 'last_modified'
FROM counted
WHERE future = 0
AND plannedFuture = 0
GROUP BY username, type

UNION 
SELECT 
    username, 
    "all",
    count(*) as 'trips',
    sum(trip_length) as 'length',
    max(last_modified) as 'last_modified'
FROM counted
WHERE future = 0
AND plannedFuture = 0
GROUP BY username
