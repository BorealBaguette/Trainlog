-- The current logo of each named operator, found by any of its spellings. Names matching no
-- operator, or one without a logo, are left out.
SELECT x.name, l.logo_url
FROM unnest(CAST(:names AS text[])) AS x(name)
JOIN LATERAL (
    SELECT al.operator_id
    FROM operator_aliases al
    WHERE al.normalized = operator_normalize(x.name)
    ORDER BY (al.operator_type = 'operator') DESC, al.operator_id
    LIMIT 1
) a ON TRUE
JOIN LATERAL (
    SELECT logo_url
    FROM operator_logos
    WHERE operator_id = a.operator_id
    ORDER BY effective_date DESC NULLS LAST, uid DESC
    LIMIT 1
) l ON TRUE;
