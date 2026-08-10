INSERT INTO commute_occurrences (commute_route_id)
VALUES (:commute_route_id)
RETURNING uid
