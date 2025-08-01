SELECT DISTINCT material_type
FROM trip
WHERE username = :username
AND type = :trip_type
