-- Two indexes fixing the slow /u/<user>/trips listing.

-- 1) Ticket lookup. get_ticket.sql counts a ticket's trips via
-- "LEFT JOIN trips ON tickets.uid = trips.ticket_id"; without an index that is a
-- sequential scan over all of trips (~740k rows, ~80ms). formatTrip runs that
-- lookup once per ticketed trip, so a trip listing of ~100 ticketed trips spent
-- ~8 of its seconds in this join alone. Partial: only ~a tenth of trips have a
-- ticket, and the join only ever probes non-NULL values.
CREATE INDEX IF NOT EXISTS idx_trips_ticket_id ON trips (ticket_id)
    WHERE ticket_id IS NOT NULL;

-- 2) Exact type filter (e.g. "type:ferry"). The listing's base CTE reads a
-- user's trips filtered by user_id; with a type filter it previously
-- materialised every one of the user's rows (up to a few thousand, scattered
-- across the heap) and only then discarded the non-matching types. Pushing the
-- trip_type equality into that scan (see get_dynamic_user_trips.sql) lets this
-- composite index fetch just the matching rows, turning a full per-user heap
-- scan into a narrow index range.
CREATE INDEX IF NOT EXISTS idx_trips_user_type ON trips (user_id, trip_type);
