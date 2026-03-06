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
),
YearlyFiltered AS (
    SELECT *,
           strftime('%Y', utc_filtered_start_datetime) AS trip_year
    FROM UTC_Filtered
)

SELECT *,
       CASE
           WHEN  
               (
                   julianday('now') > julianday(actual_end_datetime) 
                   OR utc_filtered_start_datetime = -1
               )
               AND utc_filtered_start_datetime != 1
           THEN 1
           ELSE 0
       END AS past,
       CASE
           WHEN julianday('now') BETWEEN julianday(actual_start_datetime) AND julianday(actual_end_datetime)
           THEN 1
           ELSE 0 
       END AS current,
       CASE
           WHEN julianday('now') <= julianday(actual_start_datetime)
           THEN 1
           ELSE 0 
       END AS plannedFuture,
       CASE
           WHEN utc_filtered_start_datetime = 1
           THEN 1
           ELSE 0 
       END AS future,
       count(*) AS count
FROM YearlyFiltered 
WHERE username = :username
  AND (:lastLocal = 'all' OR julianday(last_modified) > julianday(:lastLocal))
  AND (
      :public = 0
      OR (:public = 1 AND visibility = 'public')
      OR (:friend = 1 AND visibility = 'friends')
      OR (visibility IS NULL AND type IN ('train', 'air', 'bus', 'ferry', 'aerialway', 'tram', 'metro'))
  )
GROUP BY origin_station, destination_station, trip_length, trip_year, past, current, plannedFuture, future
ORDER BY start_datetime = 1 DESC, start_datetime DESC;
