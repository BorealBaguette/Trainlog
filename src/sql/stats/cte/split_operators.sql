-- One row per (trip, operator), read from the pre-resolved trip_operators table.
--
-- This replaced a recursive CTE that walked the comma-separated `trips.operator`
-- string one character at a time, for every trip, on every stats load. The parse now
-- happens once at write time (sync_trip_operators).
--
-- The join is scoped by user_id as well as trip_id: that is the leading key of
-- trip_operators_user_trip_idx, so one user's operator rows are read as a single
-- contiguous index-only range rather than one index descent per trip. Without the
-- user_id predicate this join was slower than the recursive CTE it replaced.
--
-- No display name is produced here and no grouping key is built as text; the name is
-- resolved once per result row in stats_operator.sql.
SELECT
    tv.operator_id,
    -- What counts as "one operator" when aggregating. Operators in an equivalence
    -- group (SBB/CFF/FFS) share a key so their trips add up, while each stays a
    -- separate operator with its own name and logo everywhere else.
    --
    -- Group ids are negated so they cannot collide with operator ids: both are
    -- integers from independent sequences, and grouping on an integer is markedly
    -- cheaper than on a synthesised string.
    COALESCE(-o.group_id, tv.operator_id) AS grouping_id,
    -- Only set when the spelling matched no alias, in which case it groups by itself.
    CASE WHEN tv.operator_id IS NULL THEN tv.raw_name END AS unresolved_name,
    t.trip_length, t.is_past, t.is_planned_future, t.is_project, t.trip_duration, t.carbon
FROM time_categories t
JOIN trip_operators tv
  ON tv.trip_id = t.trip_id
 AND (:user_id IS NULL OR tv.user_id = :user_id)
LEFT JOIN operators o ON o.operator_id = tv.operator_id
WHERE t.is_project IS FALSE
