-- Legs on this cost are detached automatically (plan_trips.cost_id ON DELETE SET NULL).
DELETE FROM plan_costs WHERE uid = :uid AND plan_id = :plan_id
