-- Trips grouped by line, with the operator that line mostly runs under.
--
-- Its own query rather than the generic single-column one because the operator
-- cannot come from `trips.operator`: that column holds the raw text as typed,
-- and 12,489 trips on a line carry several operators in it at once ("RATP,
-- Île-de-France Mobilités"). Taking the most common value of that string yields
-- a name no logo will ever match. `trip_operators` already holds one row per
-- (trip, operator), split and resolved at write time.
--
-- The join has to stay out of the totals: one row per trip-operator would count
-- a two-operator trip twice. So the aggregates and the operator lookup are
-- separate passes over the same filtered trips, joined by line afterwards.
{base_filter}
{time_categories}

, charted_trips AS (
    SELECT *, TRIM(line_name) AS line
    FROM time_categories
    WHERE is_project IS FALSE
      AND line_name IS NOT NULL
      AND TRIM(line_name) <> ''
      -- On flights a one- or two-character "line" is the airline's IATA code
      -- left over from an older field (UA, FR, LH) — thousands of rows that say
      -- nothing the operator chart doesn't already say. A real flight number is
      -- an IATA code plus digits.
      AND (:tripType NOT IN ('air', 'helicopter') OR length(TRIM(line_name)) >= 3)
)
, line_totals AS (
    SELECT
        line,
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
    FROM charted_trips
    GROUP BY line
)
-- Only the lines that will be returned. On the all-users view the tail is tens
-- of thousands of lines nobody will see, and resolving an operator for each of
-- them dominated the query: 27s down to 3s.
, top_lines AS (
    SELECT line FROM line_totals ORDER BY "totalTrips" DESC LIMIT 1000
)
-- How often each individual operator appears on each line. The user_id
-- predicate leads trip_operators_user_trip_idx, so one user's rows are read as
-- a contiguous range instead of one index descent per trip.
, line_operator_uses AS (
    SELECT
        t.line,
        COALESCE(o.short_name, tv.raw_name) AS operator,
        COUNT(*) AS uses
    FROM charted_trips t
    JOIN top_lines tl ON tl.line = t.line
    JOIN trip_operators tv
      ON tv.trip_id = t.trip_id
     AND (:user_id IS NULL OR tv.user_id = :user_id)
    LEFT JOIN operators o ON o.operator_id = tv.operator_id
    WHERE COALESCE(o.short_name, tv.raw_name) IS NOT NULL
    GROUP BY t.line, COALESCE(o.short_name, tv.raw_name)
)
, dominant_operator AS (
    -- Ties break on the name so the pick is stable between loads.
    SELECT DISTINCT ON (line) line, operator
    FROM line_operator_uses
    ORDER BY line, uses DESC, operator
)
SELECT
    t.line,
    d.operator,
    t."pastTrips", t."plannedFutureTrips", t."totalTrips",
    t."pastKm", t."plannedFutureKm",
    t."pastDuration", t."plannedFutureDuration",
    t."pastCO2", t."plannedFutureCO2",
    t."pastDelay", t."plannedFutureDelay"
FROM line_totals t
LEFT JOIN dominant_operator d ON d.line = t.line
ORDER BY t."totalTrips" DESC
LIMIT 1000;
