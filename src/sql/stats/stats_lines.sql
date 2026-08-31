-- Trips grouped by line AND operator.
--
-- A line name is only unique within a network: tram 1 exists in Zurich, Vienna
-- and a dozen other cities, and grouping on the name alone piled them into one
-- bar. The operator is what tells them apart, so it is part of the grouping key
-- rather than decoration hung off it afterwards.
--
-- The operator cannot come from `trips.operator`: that column holds the raw text
-- as typed, and 12,489 trips carry several operators in it at once ("RATP,
-- Île-de-France Mobilités"). Taking the most common value of that string yields
-- a name no logo will ever match. `trip_operators` already holds one row per
-- (trip, operator), split and resolved at write time.
--
-- A trip with several operators therefore lands in one bar per operator, and its
-- km are counted in each. That is a duplicate, and it is deliberate: it is
-- exactly what the operator chart already does with the same trip, so the two
-- charts agree with each other. Line 390 run by a private operator under a city
-- network shows up twice — once under the network, once under whoever actually
-- drove it — which is the truth of how it was logged.
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
-- One row per (trip, operator). LEFT JOIN, not JOIN: a trip with a line but no
-- operator logged still belongs on the chart, and comes through with a null
-- grouping_id that groups all such trips of that line together.
--
-- The user_id predicate leads trip_operators_user_trip_idx, so one user's rows
-- are read as a contiguous range instead of one index descent per trip.
, line_operators AS (
    SELECT
        t.*,
        -- Same identity rule as the operator chart: operators in an equivalence
        -- group (SBB/CFF/FFS) share a key, so a line running under two spellings
        -- of one company stays a single bar. Group ids are negated so they
        -- cannot collide with operator ids.
        COALESCE(-o.group_id, tv.operator_id) AS grouping_id,
        -- The specific operator behind the grouping key, kept so the display
        -- name can be picked from the group's members below.
        tv.operator_id,
        -- Only set when the spelling matched no alias, in which case it groups
        -- by itself.
        CASE WHEN tv.operator_id IS NULL THEN tv.raw_name END AS unresolved_name
    FROM charted_trips t
    LEFT JOIN trip_operators tv
      ON tv.trip_id = t.trip_id
     AND (:user_id IS NULL OR tv.user_id = :user_id)
    LEFT JOIN operators o ON o.operator_id = tv.operator_id
)
, line_totals AS (
    SELECT
        line,
        grouping_id,
        unresolved_name,
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
    FROM line_operators
    GROUP BY line, grouping_id, unresolved_name
)
-- Which member of a group gives the row its name: the one this user logs most,
-- as on the operator chart, so the same company reads as CFF in Geneva and SBB
-- in Zurich. Resolved once per operator after the grouping, not once per row.
, member_counts AS (
    SELECT grouping_id, operator_id, COUNT(*) AS uses
    FROM line_operators
    WHERE operator_id IS NOT NULL
    GROUP BY grouping_id, operator_id
)
, display_member AS (
    -- Ties break on the id so the pick is stable between loads.
    SELECT DISTINCT ON (grouping_id) grouping_id, operator_id
    FROM member_counts
    ORDER BY grouping_id, uses DESC, operator_id
)
SELECT
    t.line,
    -- The user's own spelling for resolved operators, the text as typed
    -- otherwise, and null for trips logged without one.
    COALESCE(o.short_name, t.unresolved_name) AS operator,
    t."pastTrips", t."plannedFutureTrips", t."totalTrips",
    t."pastKm", t."plannedFutureKm",
    t."pastDuration", t."plannedFutureDuration",
    t."pastCO2", t."plannedFutureCO2",
    t."pastDelay", t."plannedFutureDelay"
FROM line_totals t
LEFT JOIN display_member d ON d.grouping_id = t.grouping_id
LEFT JOIN operators o ON o.operator_id = d.operator_id
ORDER BY t."totalTrips" DESC
LIMIT 1000;
