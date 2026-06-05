SELECT tickets.uid, tickets.name, tickets.price, tickets.currency, tickets.purchasing_date, COUNT(trips.ticket_id) AS trip_count
FROM tickets
LEFT JOIN trips ON tickets.uid = trips.ticket_id
WHERE tickets.uid = :uid
GROUP BY tickets.uid;
