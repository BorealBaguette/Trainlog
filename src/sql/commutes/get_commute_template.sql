-- Legs of a commute's template (its first-ever occurrence), in travel order.
-- This is the stable reference "log occurrence" clones route details and
-- scheduled leg times from -- never the latest occurrence, so a delayed day
-- never drifts the template.
SELECT crt.trip_id,
       crt.sequence,
       t.origin_station,
       t.destination_station,
       t.start_datetime::time AS scheduled_start,
       t.end_datetime::time AS scheduled_end
FROM commute_route_trips crt
JOIN trips t ON t.trip_id = crt.trip_id
WHERE crt.occurrence_id = (
    SELECT MIN(uid) FROM commute_occurrences WHERE commute_route_id = :commute_route_id
)
ORDER BY crt.sequence
