SELECT DISTINCT material_type
FROM trips
WHERE user_id = :user_id
AND trip_type = :trip_type
