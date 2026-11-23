-- Attach (or detach with :cost_id = NULL) a leg to a shared cost.
UPDATE plan_trips
SET cost_id = :cost_id,
    last_modified = :last_modified
WHERE uid = :uid AND plan_id = :plan_id
  AND linked_trip_id IS NULL  -- a linked trip is edited as the trip it is
