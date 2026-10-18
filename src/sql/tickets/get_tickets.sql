-- A clipping card's uses, and whether used up it is still active, are counted in Python
-- (src/tickets.py): a use can span several trips.
SELECT
    uid,
    name,
    price,
    currency,
    to_char(purchasing_date, 'YYYY-MM-DD') AS purchasing_date,
    to_char(valid_from, 'YYYY-MM-DD') AS valid_from,
    to_char(valid_until, 'YYYY-MM-DD') AS valid_until,
    uses,
    use_minutes,
    CASE
        WHEN valid_from IS NULL AND valid_until IS NULL THEN active
        ELSE CURRENT_DATE BETWEEN COALESCE(valid_from, CURRENT_DATE) AND COALESCE(valid_until, CURRENT_DATE)
    END AS active,
    auto_renew,
    operator,
    secondary_operators,
    product,
    active_countries,
    notes
FROM tickets
WHERE username = :username AND (CAST(:uid AS integer) IS NULL OR uid = :uid)
ORDER BY COALESCE(valid_from, purchasing_date) DESC, uid DESC;
