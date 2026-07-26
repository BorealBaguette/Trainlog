-- Resolve a batch of written operator names to canonical operators, for callers that
-- hold names rather than trip ids (autocomplete, transit routing, the admin panel).
--
-- Normalisation happens in SQL rather than in Python so there is exactly one
-- definition of what makes two spellings equal — the same operator_normalize() that
-- backs the unique index on operator_aliases.
--
-- Pool handling matches insert_trip_operators.sql: :operator_type is preferred, but
-- a match in another pool is still returned when that pool has none.
--
-- Returns one row per input name; operator_id is NULL when nothing matched.
SELECT x.name AS input_name,
       o.operator_id,
       o.short_name,
       o.long_name,
       o.slug,
       o.operator_type
FROM unnest(CAST(:names AS text[])) AS x(name)
LEFT JOIN LATERAL (
    SELECT al.operator_id
    FROM operator_aliases al
    WHERE al.normalized = operator_normalize(x.name)
    ORDER BY (al.operator_type = :operator_type) DESC, al.operator_id
    LIMIT 1
) a ON TRUE
LEFT JOIN operators o ON o.operator_id = a.operator_id;
