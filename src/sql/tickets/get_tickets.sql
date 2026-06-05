SELECT
    tickets.uid,
    tickets.name,
    tickets.price,
    tickets.currency,
    tickets.purchasing_date,
    tickets.notes,
    tickets.active,
    tickets.active_countries,
    COUNT(trips.ticket_id) AS trip_count,
    tickets.price / NULLIF(COUNT(trips.ticket_id), 0) AS price_per_trip,
    tickets.price / NULLIF(SUM(trips.trip_length / 1000.0), 0) AS price_per_km,
    string_agg(trips.trip_id::text, ',') AS trip_ids
FROM tickets
LEFT JOIN trips ON tickets.uid = trips.ticket_id
WHERE tickets.username = :username
GROUP BY tickets.uid
ORDER BY tickets.purchasing_date DESC;
