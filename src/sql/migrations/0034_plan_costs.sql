-- Plan shared costs: one price split over several legs (rental car, rail pass, ...).
-- Mirrors the rendering subset of `tickets`; on validation each becomes a real ticket
-- linked to the created trips that were on it. plan_trips.cost_id attaches a leg to a
-- cost (NULL when the cost is deleted). IF NOT EXISTS so it's a no-op where these were
-- already hand-applied in dev.
CREATE TABLE IF NOT EXISTS plan_costs (
    uid             SERIAL PRIMARY KEY,
    plan_id         INTEGER NOT NULL REFERENCES plans(uid) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    currency        TEXT,
    notes           TEXT,
    created         TIMESTAMP,
    last_modified   TIMESTAMP
);
CREATE INDEX IF NOT EXISTS plan_costs_plan_id_idx ON plan_costs (plan_id);

ALTER TABLE plan_trips
    ADD COLUMN IF NOT EXISTS cost_id INTEGER REFERENCES plan_costs(uid) ON DELETE SET NULL;
