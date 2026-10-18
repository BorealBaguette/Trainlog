-- A trip may ride on several tickets (a pass and a seat reservation, a Deutschlandticket and
-- an ICE supplement): the main one first, then the extras. Each ticket's price is still
-- shared among its own trips; the trip's cost is its own price and its share of each.
ALTER TABLE trips ADD COLUMN IF NOT EXISTS ticket_ids INTEGER[] NOT NULL DEFAULT '{}';
UPDATE trips SET ticket_ids = ARRAY[ticket_id] WHERE ticket_id IS NOT NULL;
DROP INDEX IF EXISTS idx_trips_ticket_id;
ALTER TABLE trips DROP COLUMN ticket_id;
-- The trips of a ticket: ticket_ids @> ARRAY[uid], which the index serves (= ANY does not).
CREATE INDEX IF NOT EXISTS idx_trips_ticket_ids ON trips USING GIN (ticket_ids);
