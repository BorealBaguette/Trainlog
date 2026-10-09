{base_filter}
{time_categories}

-- A route is the same both ways; each end is its quai key where the trip has one (whatever
-- it was called then), else its name.
, ends AS (
    SELECT *,
        COALESCE(origin_station_key, origin_station) AS origin_id,
        COALESCE(destination_station_key, destination_station) AS destination_id
    FROM time_categories
)
, routes AS (
    SELECT *,
        CASE WHEN origin_id <= destination_id THEN origin_station ELSE destination_station END AS a_name,
        CASE WHEN origin_id <= destination_id THEN destination_station ELSE origin_station END AS b_name,
        LEAST(origin_id, destination_id) AS a_id,
        GREATEST(origin_id, destination_id) AS b_id
    FROM ends
)
SELECT 
    -- Each end shown by the name most trips there bear.
    jsonb_build_array(
        mode() WITHIN GROUP (ORDER BY a_name),
        mode() WITHIN GROUP (ORDER BY b_name)
    )::text AS route,
    SUM(is_past) AS "pastTrips",
    SUM(is_planned_future) AS "plannedFutureTrips",
    SUM(is_past + is_planned_future) AS "count",
    SUM(trip_length * is_past) AS "pastKm",
    SUM(trip_length * is_planned_future) AS "plannedFutureKm",
    SUM(trip_duration * is_past) AS "pastDuration",
    SUM(trip_duration * is_planned_future) AS "plannedFutureDuration",
    SUM(carbon * is_past) AS "pastCO2",
    SUM(carbon * is_planned_future) AS "plannedFutureCO2",
    SUM(COALESCE(arrival_delay, 0) * is_past) AS "pastDelay",
    SUM(COALESCE(arrival_delay, 0) * is_planned_future) AS "plannedFutureDelay",
    SUM((COALESCE(arrival_delay, 0) - COALESCE(departure_delay, 0)) * is_past) AS "pastDelayAccumulated",
    SUM((COALESCE(arrival_delay, 0) - COALESCE(departure_delay, 0)) * is_planned_future) AS "plannedFutureDelayAccumulated"
FROM routes
GROUP BY a_id, b_id
ORDER BY "count" DESC
-- Capped well below the old 10000: the page charts the top 10 and the
-- fullscreen view scrolls 20 rows at a time, so 1000 is ~50 screens of
-- depth. The tail was pure payload — it made a heavy user's stats response
-- several megabytes of rows nothing ever drew.
LIMIT 1000;