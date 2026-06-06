-- Index operator_logos for the era-appropriate-logo lookup. Both trip queries
-- (get_trip.sql, get_dynamic_user_trips.sql) run a correlated subquery per trip:
--   SELECT logo_url FROM operator_logos
--   WHERE operator_id = ? AND (effective_date <= ? OR effective_date IS NULL ...)
--   ORDER BY effective_date DESC NULLS LAST, uid DESC LIMIT 1
-- The subquery correlates on the trip's start datetime, so it can't be memoized
-- across rows; without an index it sequentially scanned the whole operator_logos
-- table once per trip (~4800 rows discarded each time), dominating the query for
-- users with many trips. This index matches the filter (operator_id) and the
-- ORDER BY so each lookup is a single index scan.
CREATE INDEX IF NOT EXISTS idx_operator_logos_operator_id_effective_date
    ON operator_logos (operator_id, effective_date DESC NULLS LAST, uid DESC);
