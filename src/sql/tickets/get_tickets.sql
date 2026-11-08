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
    to_char(valid_on, 'YYYY-MM-DD') AS valid_on,
    uses,
    use_minutes,
    -- A single ticket for a given day (valid_on) is valid on that day only.
    CASE
        WHEN valid_from IS NULL AND valid_until IS NULL AND valid_on IS NULL THEN active
        ELSE CURRENT_DATE BETWEEN COALESCE(valid_from, valid_on, CURRENT_DATE)
            AND COALESCE(valid_until, valid_on, CURRENT_DATE)
    END AS active,
    auto_renew,
    operator,
    secondary_operators,
    product,
    active_countries,
    notes
FROM tickets
WHERE username = :username AND (CAST(:uid AS integer) IS NULL OR uid = :uid)
ORDER BY COALESCE(valid_from, valid_on, purchasing_date) DESC, uid DESC;
