-- The highest leg sequence number on a commute's template (first occurrence),
-- so append_legs() knows where to continue numbering. 0 if the commute has
-- no legs yet (shouldn't happen in practice -- create_commute always adds at
-- least one -- but keeps this safe to call regardless).
SELECT COALESCE(MAX(crt.sequence), 0) AS max_sequence
FROM commute_route_trips crt
JOIN commute_occurrences co ON crt.occurrence_id = co.uid
WHERE co.commute_route_id = :commute_route_id
  AND co.uid = (
      SELECT MIN(uid) FROM commute_occurrences WHERE commute_route_id = :commute_route_id
  )
