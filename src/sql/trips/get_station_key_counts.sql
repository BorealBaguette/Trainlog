-- How many of a user's trips of a type start or end at each quai station, by its key: the
-- station whatever the trips called it.
SELECT station_key, COUNT(*) AS total_occurrences
FROM (
    SELECT origin_station_key AS station_key
    FROM trips
    WHERE user_id = :user_id AND trip_type = :trip_type
    UNION ALL
    SELECT destination_station_key
    FROM trips
    WHERE user_id = :user_id AND trip_type = :trip_type
) AS ends
WHERE station_key IS NOT NULL
GROUP BY station_key
