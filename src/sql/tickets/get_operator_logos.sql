-- Every logo of each named operator, found by any of its spellings, newest first: each ticket
-- shows the one in force on its date. Names matching no operator, or one without a logo, are
-- left out.
SELECT x.name, l.effective_date, l.logo_url
FROM unnest(CAST(:names AS text[])) AS x(name)
JOIN LATERAL (
    SELECT al.operator_id
    FROM operator_aliases al
    WHERE al.normalized = operator_normalize(x.name)
    ORDER BY (al.operator_type = 'operator') DESC, al.operator_id
    LIMIT 1
) a ON TRUE
JOIN operator_logos l ON l.operator_id = a.operator_id
ORDER BY x.name, l.effective_date DESC NULLS LAST, l.uid DESC;
