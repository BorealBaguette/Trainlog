{base_filter}
{time_categories}

SELECT 
    EXTRACT(YEAR FROM filtered_datetime)::text AS year,
    SUM(is_past) AS "pastTrips",
    SUM(is_planned_future) AS "plannedFutureTrips",
    SUM(trip_length * is_past) AS "pastKm",
    SUM(trip_length * is_planned_future) AS "plannedFutureKm",
    SUM(trip_duration * is_past) AS "pastDuration",
    SUM(trip_duration * is_planned_future) AS "plannedFutureDuration",
    SUM(carbon * is_past) AS "pastCO2",
    SUM(carbon * is_planned_future) AS "plannedFutureCO2",
    SUM(COALESCE(arrival_delay, 0) * is_past) AS "pastDelay",
    SUM(COALESCE(arrival_delay, 0) * is_planned_future) AS "plannedFutureDelay"
FROM time_categories
WHERE EXTRACT(YEAR FROM filtered_datetime) > 1950
-- Upper bound tracks today rather than a fixed year, so real future plans
-- stay in while mistyped dates (3000, 9999) stay out.
AND EXTRACT(YEAR FROM filtered_datetime) <= EXTRACT(YEAR FROM NOW()) + 15
GROUP BY year;