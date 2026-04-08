{base_filter}
{time_categories}

SELECT 
    EXTRACT(YEAR FROM filtered_datetime)::text AS year,
    EXTRACT(MONTH FROM filtered_datetime)::text AS month,
    SUM(is_past) AS "pastTrips",
    SUM(is_planned_future) AS "plannedFutureTrips",
    SUM(trip_length * is_past) AS "pastKm",
    SUM(trip_length * is_planned_future) AS "plannedFutureKm",
    SUM(trip_duration * is_past) AS "pastDuration",
    SUM(trip_duration * is_planned_future) AS "plannedFutureDuration",
    SUM(carbon * is_past) AS "pastCO2",
    SUM(carbon * is_planned_future) AS "plannedFutureCO2"
FROM time_categories
WHERE EXTRACT(YEAR FROM filtered_datetime) > 1950
AND EXTRACT(YEAR FROM filtered_datetime) < 2100
GROUP BY year, month;
