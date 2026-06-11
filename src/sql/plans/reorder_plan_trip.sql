-- Set the sort order of one plan-trip (called per row from a reorder request).
UPDATE plan_trips
SET sort_order = :sort_order
WHERE uid = :uid AND plan_id = :plan_id AND user_id = :user_id
