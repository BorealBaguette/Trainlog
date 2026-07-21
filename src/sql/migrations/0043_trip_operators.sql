-- Derived link between trips and canonical operators.
--
-- `trips.operator` stays the source of truth: free text, comma-separated, edited by
-- the user and exported verbatim to CSV. It was however re-parsed on every read —
-- a recursive CTE in the stats grouping, regexp_split_to_table in the dashboard, a
-- Python split issuing one query per operator per trip on the trip page, and
-- split_part(operator, ',', 1) in the trip list (which silently resolved only the
-- first operator of a multi-operator trip).
--
-- This table holds that parse once, already resolved through operator_aliases, so
-- reads become an indexed join. It is purely derived: it can be rebuilt from
-- `trips.operator` at any time with rebuild_trip_operators() (src/operators.py), and
-- is refreshed by sync_trip_operators() from the trip write paths.

CREATE TABLE trip_operators (
    trip_id     INTEGER NOT NULL REFERENCES trips (trip_id) ON DELETE CASCADE,
    -- 0-based, in the order the names were typed.
    position    SMALLINT NOT NULL,
    -- Denormalised from trips. Every aggregate read here is per-user, so this is the
    -- leading index key: it puts one user's rows in a single contiguous range that
    -- can be read with one index descent instead of one descent per trip. Immutable
    -- in practice — a trip never changes owner; duplicating to another user creates
    -- new rows through sync_trip_operators().
    user_id     INTEGER NOT NULL,
    -- The name as typed, e.g. 'CFF'. Trips display trips.operator, not this; it is
    -- kept so the admin panel can report unresolved spellings by occurrence.
    raw_name    TEXT NOT NULL,
    -- NULL when the spelling matches no alias — that is the admin's work queue.
    operator_id INTEGER REFERENCES operators (operator_id) ON DELETE SET NULL,
    PRIMARY KEY (trip_id, position)
);

-- Stats and dashboard group by operator_id; the partial index serves the admin
-- "unresolved spellings" queue.
CREATE INDEX trip_operators_operator_id_idx ON trip_operators (operator_id);
CREATE INDEX trip_operators_unresolved_idx ON trip_operators (raw_name)
    WHERE operator_id IS NULL;

-- The index the aggregate reads live on. Leading on user_id makes one user's rows a
-- single contiguous range, and INCLUDE-ing the payload makes the read index-only, so
-- the stats grouping fetches a user's whole operator set with one index descent.
--
-- This matters more than it looks. Keyed on trip_id instead, the same join needed one
-- B-tree descent per trip (19,759 buffer hits for a 6.3k-trip user, and the planner
-- would otherwise sequentially scan all 663k rows); keyed on user_id it is 39 buffers.
CREATE INDEX trip_operators_user_trip_idx
    ON trip_operators (user_id, trip_id, position) INCLUDE (raw_name, operator_id);

-- The planner needs statistics on this table immediately: the first stats query
-- after a deploy would otherwise plan against a default estimate.
ANALYZE trip_operators;

-- Backfill. Frozen copy of src/sql/operators/insert_trip_operators.sql, which is
-- what sync_trip_operators() / rebuild_trip_operators() run — keep the two in step.
--
-- The trip's operator pool is a preference, not a filter: an alias in the trip's own
-- pool wins, but a match in another pool is still used when that pool has none, so
-- no trip loses a logo it used to show.
--
-- DISTINCT ON collapses names that resolve to the same operator within one trip, so
-- a trip logged as 'SBB, CFF' counts once for SBB rather than twice once those two
-- spellings are aliased together. Unresolved names dedupe on their normalised form.
INSERT INTO trip_operators (trip_id, position, user_id, raw_name, operator_id)
SELECT trip_id,
       (row_number() OVER (PARTITION BY trip_id ORDER BY ord) - 1)::smallint,
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
