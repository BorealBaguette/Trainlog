UPDATE plan_costs
SET name = :name,
    price = :price,
    currency = :currency,
    notes = :notes,
    last_modified = :last_modified
WHERE uid = :uid AND plan_id = :plan_id
