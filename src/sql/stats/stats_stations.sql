{base_filter}
{time_categories}

, stations AS (
    -- A station is its quai key where the trip has one (whatever it was called then),
    -- else its name.
    SELECT 
        origin_station AS station, 
        COALESCE(origin_station_key, origin_station) AS station_id,
        is_past, 
        is_planned_future, 
        trip_length,
        trip_duration,
        carbon,
        arrival_delay,
        departure_delay
    FROM time_categories
    UNION ALL
    SELECT 
        destination_station AS station, 
        COALESCE(destination_station_key, destination_station) AS station_id,
        is_past, 
        is_planned_future, 
        trip_length,
        trip_duration,
        carbon,
        arrival_delay,
        departure_delay
    FROM time_categories
)
SELECT 
    -- Shown by the name most trips there bear.
    mode() WITHIN GROUP (ORDER BY station) AS station,
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
FROM stations
GROUP BY station_id
ORDER BY count DESC
-- Capped well below the old 10000: the page charts the top 10 and the
-- fullscreen view scrolls 20 rows at a time, so 1000 is ~50 screens of
-- depth. The tail was pure payload — it made a heavy user's stats response
-- several megabytes of rows nothing ever drew.
LIMIT 1000;