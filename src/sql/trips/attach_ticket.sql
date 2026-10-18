-- The tickets given added after those the trip has, in their order, each once; or, replacing,
-- in their place (none detaching them all).
UPDATE trips SET
    ticket_ids = CASE WHEN :replace THEN CAST(:ticket_ids AS integer[])
        ELSE ticket_ids || ARRAY(
            SELECT given.uid
            FROM unnest(CAST(:ticket_ids AS integer[])) WITH ORDINALITY AS given(uid, position)
            WHERE NOT given.uid = ANY(trips.ticket_ids)
            ORDER BY given.position
        )
    END,
    last_modified = :last_modified
WHERE trip_id = :trip_id;
