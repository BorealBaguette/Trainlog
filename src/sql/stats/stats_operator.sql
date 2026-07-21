{base_filter}
{time_categories}
{split_operators}

-- Aggregate on the identity of the operator first, then resolve its display name.
-- Joining `operators` after the GROUP BY touches one row per operator in the result
-- (a few hundred) instead of one per trip-operator row (tens of thousands).
, operator_totals AS (
    SELECT
        grouping_id,
        unresolved_name,
        SUM(is_past) AS "pastTrips",
        SUM(is_planned_future) AS "plannedFutureTrips",
        SUM(is_past + is_planned_future) AS "totalTrips",
        SUM(trip_length * is_past) AS "pastKm",
        SUM(trip_length * is_planned_future) AS "plannedFutureKm",
        SUM(trip_length * (is_past + is_planned_future)) AS "totalKm",
        SUM(trip_duration * is_past) AS "pastDuration",
        SUM(trip_duration * is_planned_future) AS "plannedFutureDuration",
        SUM(trip_duration * (is_past + is_planned_future)) AS "totalDuration",
        SUM(carbon * is_past) AS "pastCO2",
        SUM(carbon * is_planned_future) AS "plannedFutureCO2",
        SUM(carbon * (is_past + is_planned_future)) AS "totalCO2"
    FROM split_operators
    -- Exactly one of the two is non-null per row, so each operator (or group, or
    -- unmatched spelling) is counted once.
    GROUP BY grouping_id, unresolved_name
)
-- Which member of a group gives the row its name: the one this user logs most.
-- A group has no canonical member — SBB, CFF and FFS are equally real — so rather
-- than picking a winner globally, each user sees the group under their own flavour,
-- with identical totals either way. For an ungrouped operator there is only ever one
-- candidate, and for the all-users view (:user_id IS NULL) it resolves to the most
-- commonly logged spelling overall.
, member_counts AS (
    SELECT grouping_id, operator_id, COUNT(*) AS uses
    FROM split_operators
    WHERE operator_id IS NOT NULL
    GROUP BY grouping_id, operator_id
)
, display_member AS (
    SELECT DISTINCT ON (grouping_id) grouping_id, operator_id
    FROM member_counts
    ORDER BY grouping_id, uses DESC, operator_id
)
SELECT
    -- The user's own spelling for resolved operators, the text as typed otherwise.
    COALESCE(o.short_name, t.unresolved_name) AS operator,
    d.operator_id,
    o.long_name,
    t."pastTrips", t."plannedFutureTrips", t."totalTrips",
    t."pastKm", t."plannedFutureKm", t."totalKm",
    t."pastDuration", t."plannedFutureDuration", t."totalDuration",
    t."pastCO2", t."plannedFutureCO2", t."totalCO2"
FROM operator_totals t
LEFT JOIN display_member d ON d.grouping_id = t.grouping_id
LEFT JOIN operators o ON o.operator_id = d.operator_id
ORDER BY t."totalTrips" DESC;
