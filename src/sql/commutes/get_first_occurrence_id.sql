SELECT MIN(uid) AS occurrence_id
FROM commute_occurrences
WHERE commute_route_id = :commute_route_id
