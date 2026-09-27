-- How many distinct stations this user visited in the year, counting every spelling of a
-- registered station once. Unresolved spellings count as themselves.
WITH base_filter AS (
    SELECT *, COALESCE(utc_start_datetime, start_datetime) AS filtered_datetime
    FROM trips
    WHERE (:tripType = 'combined' OR trip_type = :tripType)
    AND user_id = :user_id
    AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime))::text = :year
    AND is_project = false
    AND COALESCE(utc_start_datetime, start_datetime) < NOW()
),
endpoints AS (
    SELECT station_normalize(origin_station) AS normalized,
           station_type_bucket(trip_type) AS station_type,
           origin_station AS label
    FROM base_filter WHERE origin_station IS NOT NULL
    UNION ALL
    SELECT station_normalize(destination_station),
           station_type_bucket(trip_type),
           destination_station
    FROM base_filter WHERE destination_station IS NOT NULL
)
SELECT COUNT(DISTINCT COALESCE('#' || sl.station_id::text, e.normalized, e.label))
           AS unique_stations
FROM endpoints e
LEFT JOIN station_labels sl
       ON sl.normalized = e.normalized
      AND sl.station_type = e.station_type
      AND sl.station_id IS NOT NULL
