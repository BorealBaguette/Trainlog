SELECT
    station,
    SUM(count) AS total_occurrences
FROM (
    SELECT origin_station AS station, COUNT(*) AS count
    FROM trips
    WHERE user_id = :user_id AND trip_type = :trip_type
    GROUP BY origin_station
    UNION ALL
    SELECT destination_station AS station, COUNT(*) AS count
    FROM trips
    WHERE user_id = :user_id AND trip_type = :trip_type
    GROUP BY destination_station
) AS combined
GROUP BY station
ORDER BY total_occurrences DESC
