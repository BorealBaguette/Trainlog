-- Tickets by uid, as a trip shows them.
SELECT uid, name, price, currency, purchasing_date
FROM tickets
WHERE uid = ANY(CAST(:uids AS integer[]));
