-- One row per commute the user could append more legs to, with the template's
-- first origin / last destination so near-duplicate commutes (e.g. two
-- routes that both start from the same station) stay distinguishable in a
-- picker. Not filtered by any "round trip" status -- any commute can always
-- have more legs appended.
SELECT c.uid,
       c.name,
       (SELECT t.origin_station
        FROM commute_occurrences co0
        JOIN commute_route_trips crt0 ON crt0.occurrence_id = co0.uid
        JOIN trips t ON t.trip_id = crt0.trip_id
        WHERE co0.commute_route_id = c.uid
        ORDER BY co0.uid ASC, crt0.sequence ASC
        LIMIT 1) AS origin_station,
       (SELECT t.destination_station
        FROM commute_occurrences co0
        JOIN commute_route_trips crt0 ON crt0.occurrence_id = co0.uid
        JOIN trips t ON t.trip_id = crt0.trip_id
        WHERE co0.commute_route_id = c.uid
        ORDER BY co0.uid ASC, crt0.sequence DESC
        LIMIT 1) AS destination_station
FROM commute_routes c
WHERE c.user_id = :user_id AND c.archived = FALSE
ORDER BY c.name
