{base_filter}
{time_categories}

-- Endpoints are grouped by the station their spelling resolves to, so "Gare de Lens" and
-- "Lens" are one row. Unresolved labels group by their own spelling.
--
-- Resolved through a join rather than a per-row helper function, which is orders of
-- magnitude slower here. MATERIALIZED so station_normalize() runs once per endpoint.
, endpoints AS MATERIALIZED (
    SELECT
        station_normalize(origin_station)  AS normalized,
        station_type_bucket(trip_type)     AS station_type,
        origin_station                     AS label,
        is_past, is_planned_future, trip_length, trip_duration, carbon, arrival_delay,
        departure_delay
    FROM time_categories
    UNION ALL
    SELECT
        station_normalize(destination_station),
        station_type_bucket(trip_type),
        destination_station,
        is_past, is_planned_future, trip_length, trip_duration, carbon, arrival_delay,
        departure_delay
    FROM time_categories
)
SELECT
    -- Every row of a group produces the same name.
    MAX(COALESCE(
        station_flag_prefix(e.label) || station_display_name(
            s.curated_name, s.name_intl, s.name_local, s.names,
            :station_display, :user_lang
        ),
        e.label
    )) AS station,
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
FROM endpoints e
-- Filtering on resolved labels lets the join use station_labels_resolved_key.
LEFT JOIN station_labels sl
       ON sl.normalized = e.normalized
      AND sl.station_type = e.station_type
      AND sl.station_id IS NOT NULL
LEFT JOIN stations s ON s.station_id = sl.station_id
-- Keyed on the station, not the displayed name: two stations can share a name.
GROUP BY COALESCE('#' || s.station_id::text, e.normalized, e.label)
ORDER BY count DESC
-- Capped well below the old 10000: the page charts the top 10 and the
-- fullscreen view scrolls 20 rows at a time, so 1000 is ~50 screens of
-- depth. The tail was pure payload — it made a heavy user's stats response
-- several megabytes of rows nothing ever drew.
LIMIT 1000;
