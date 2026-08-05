{base_filter}
{time_categories}

SELECT 
    jsonb_build_array(
        LEAST(origin_station, destination_station), 
        GREATEST(origin_station, destination_station)
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
    SUM(COALESCE(arrival_delay, 0) * is_planned_future) AS "plannedFutureDelay"
FROM time_categories
GROUP BY LEAST(origin_station, destination_station), GREATEST(origin_station, destination_station)
ORDER BY "count" DESC
-- Capped well below the old 10000: the page charts the top 10 and the
-- fullscreen view scrolls 20 rows at a time, so 1000 is ~50 screens of
-- depth. The tail was pure payload — it made a heavy user's stats response
-- several megabytes of rows nothing ever drew.
LIMIT 1000;