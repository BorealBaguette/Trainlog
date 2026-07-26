-- Shared costs for a plan, with how many legs each covers and the per-leg share.
SELECT c.uid, c.plan_id, c.name, c.price, c.currency, c.notes,
       COUNT(pt.uid) AS leg_count,
       c.price / NULLIF(COUNT(pt.uid), 0) AS price_per_leg
FROM plan_costs c
LEFT JOIN plan_trips pt ON pt.cost_id = c.uid
WHERE c.plan_id = :plan_id
GROUP BY c.uid
ORDER BY c.uid
