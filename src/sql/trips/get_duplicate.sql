-- Find an existing trip identical on route + datetimes (NULL-safe for sentinels).
SELECT trip_id AS uid
FROM trips
WHERE origin_station = :orig
  AND destination_station = :dest
  AND start_datetime IS NOT DISTINCT FROM :start_datetime
  AND end_datetime IS NOT DISTINCT FROM :end_datetime
  AND user_id = :user_id
