-- Plan sharing + partial validation.
--
-- 1. plans.visibility — per-plan read access for /public/plan/<uuid>, mirroring the
--    three trip levels (public / friends / private). Until now a plan's share link
--    worked for anyone as soon as its author had public trips; the setting now lives
--    on the plan itself, and every plan — existing ones included — starts 'private'.
--    A plan is a draft, so sharing it is a deliberate act: its author opens it up in
--    the plan's settings once they mean to.
--
-- 2. Partial validation — legs can be logged as real trips a few at a time. A logged
--    leg keeps its row and points at the trip it produced (validated_trip_id), so it
--    is never logged twice; the plan remembers the tag every batch is filed under
--    (validated_tag_uuid) and each shared cost the ticket it was exported as
--    (plan_costs.ticket_id), so successive batches join the same tag / ticket instead
--    of duplicating them. The plan is only archived once no unlogged leg is left.

ALTER TABLE plans ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'private';
ALTER TABLE plans ADD CONSTRAINT plans_visibility_check
    CHECK (visibility IN ('public', 'friends', 'private'));

ALTER TABLE plans ADD COLUMN IF NOT EXISTS validated_tag_uuid TEXT;
-- ON DELETE SET NULL: deleting the trip a leg produced un-logs the leg rather than
-- leaving it pointing at nothing — it can then be logged again.
ALTER TABLE plan_trips ADD COLUMN IF NOT EXISTS validated_trip_id INTEGER
    REFERENCES trips(trip_id) ON DELETE SET NULL;
ALTER TABLE plan_costs ADD COLUMN IF NOT EXISTS ticket_id INTEGER
    REFERENCES tickets(uid) ON DELETE SET NULL;
