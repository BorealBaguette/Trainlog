-- Repair operators whose own name was stored as a plain 'alias' row (or lost).
--
-- The reconcile in _reconcile_name_aliases used to create the short/long alias rows
-- with INSERT ... ON CONFLICT DO NOTHING. The unique index allows only one row per
-- (operator_type, normalized), so when a plain 'alias' row already sat on that slot,
-- no 'short'/'long' row was created — the operator's own name ended up in the
-- deletable alias list. Deleting it (understandably, since it looked redundant next
-- to the Short Name column) removed the only row resolving that spelling and orphaned
-- every trip using it.
--
-- This makes every operator's short_name a 'short' row and long_name a 'long' row,
-- promoting a mis-classified row or recreating a deleted one. Idempotent.

-- 1. Promote a row equal to its operator's short_name to kind 'short'.
UPDATE operator_aliases a
SET kind = 'short'
FROM operators o
WHERE a.operator_id = o.operator_id
  AND a.normalized = operator_normalize(o.short_name)
  AND a.kind <> 'short';

-- 2. Promote a row equal to its operator's long_name (when distinct from the short) to
--    kind 'long'.
UPDATE operator_aliases a
SET kind = 'long'
FROM operators o
WHERE a.operator_id = o.operator_id
  AND a.normalized = operator_normalize(o.long_name)
  AND a.normalized IS DISTINCT FROM operator_normalize(o.short_name)
  AND a.kind <> 'long';

-- 3. Recreate a missing 'short' row (its name has no alias row at all — e.g. an admin
--    deleted it, orphaning the trips). ON CONFLICT skips names owned by another
--    operator, which is a genuine clash to resolve by hand, not here.
INSERT INTO operator_aliases (operator_id, operator_type, alias, kind)
SELECT o.operator_id, o.operator_type, o.short_name, 'short'
FROM operators o
WHERE operator_normalize(o.short_name) IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM operator_aliases a
      WHERE a.operator_id = o.operator_id
        AND a.normalized = operator_normalize(o.short_name)
  )
ON CONFLICT (operator_type, normalized) DO NOTHING;

-- 4. Same for a missing 'long' row.
INSERT INTO operator_aliases (operator_id, operator_type, alias, kind)
SELECT o.operator_id, o.operator_type, o.long_name, 'long'
FROM operators o
WHERE operator_normalize(o.long_name) IS NOT NULL
  AND operator_normalize(o.long_name) IS DISTINCT FROM operator_normalize(o.short_name)
  AND NOT EXISTS (
      SELECT 1 FROM operator_aliases a
      WHERE a.operator_id = o.operator_id
        AND a.normalized = operator_normalize(o.long_name)
  )
ON CONFLICT (operator_type, normalized) DO NOTHING;

-- The alias fixes above change what some trips resolve to (a recreated name re-attaches
-- its orphaned trips), so rebuild the derived table. Same statement as the 0043
-- backfill; kept as one block so a fresh deploy self-heals without a manual rebuild.
DELETE FROM trip_operators;
INSERT INTO trip_operators (trip_id, position, user_id, raw_name, operator_id)
SELECT trip_id,
       (row_number() OVER (PARTITION BY trip_id ORDER BY ord) - 1)::smallint AS position,
       user_id,
       raw_name,
       operator_id
FROM (
    SELECT DISTINCT ON (t.trip_id, COALESCE(a.operator_id::text, operator_normalize(x.raw)))
           t.trip_id,
           x.ord,
           t.user_id,
           TRIM(x.raw) AS raw_name,
           a.operator_id
    FROM trips t
    CROSS JOIN LATERAL unnest(string_to_array(t.operator, ',')) WITH ORDINALITY AS x(raw, ord)
    LEFT JOIN LATERAL (
        SELECT al.operator_id
        FROM operator_aliases al
        WHERE al.normalized = operator_normalize(x.raw)
        ORDER BY (al.operator_type = operator_type_bucket(t.trip_type)) DESC, al.operator_id
        LIMIT 1
    ) a ON TRUE
    WHERE operator_normalize(x.raw) IS NOT NULL
    ORDER BY t.trip_id,
             COALESCE(a.operator_id::text, operator_normalize(x.raw)),
             x.ord
) deduped;

ANALYZE trip_operators;
