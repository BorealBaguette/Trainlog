-- Generic "group the trips by one text column" stats query.
--
-- Used for every dimension that is a plain column on `trips` with no join and no
-- splitting to do: line names, vehicle registrations, power types. The column and
-- its output alias are substituted at import time from a fixed table in
-- src/sql/stats/__init__.py — they never come from a request.
--
-- `{group_expr}` is the normalised value used for BOTH grouping and display, so a
-- dimension that needs case folding (registrations: N532CH vs N532Ch are one
-- airframe) folds once and cannot disagree with itself.
{base_filter}
{time_categories}

SELECT
    {group_expr} AS {alias},
    -- Optional extra aggregates, e.g. the operator a line mostly runs under or
    -- the aircraft type a registration mostly flies as. Used to hang a logo or
    -- a silhouette off the row; empty for dimensions that have no such thing.
    {extra_select}
    SUM(is_past) AS "pastTrips",
    SUM(is_planned_future) AS "plannedFutureTrips",
    SUM(is_past + is_planned_future) AS "totalTrips",
    SUM(trip_length * is_past) AS "pastKm",
    SUM(trip_length * is_planned_future) AS "plannedFutureKm",
    SUM(trip_duration * is_past) AS "pastDuration",
    SUM(trip_duration * is_planned_future) AS "plannedFutureDuration",
    SUM(carbon * is_past) AS "pastCO2",
    SUM(carbon * is_planned_future) AS "plannedFutureCO2",
    SUM(COALESCE(arrival_delay, 0) * is_past) AS "pastDelay",
    SUM(COALESCE(arrival_delay, 0) * is_planned_future) AS "plannedFutureDelay"
FROM time_categories
WHERE is_project IS FALSE
  AND {column} IS NOT NULL
  AND TRIM({column}) <> ''
  {extra_where}
GROUP BY {group_expr}
ORDER BY "totalTrips" DESC
-- Capped well below the old 10000: the page charts the top 10 and the
-- fullscreen view scrolls 20 rows at a time, so 1000 is ~50 screens of
-- depth. The tail was pure payload — it made a heavy user's stats response
-- several megabytes of rows nothing ever drew.
LIMIT 1000;
