-- A shared cost on a plan (rental car, rail pass, ...).
INSERT INTO plan_costs (plan_id, name, price, currency, notes, created, last_modified)
VALUES (:plan_id, :name, :price, :currency, :notes, :created, :last_modified)
RETURNING uid
