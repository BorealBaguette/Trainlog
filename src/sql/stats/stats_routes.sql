{base_filter}
{time_categories}

-- Routes are grouped by resolved station, like stats_stations.sql. MATERIALIZED so the
-- keys are computed once rather than for each use in LEAST/GREATEST and GROUP BY.
, resolved AS MATERIALIZED (
    SELECT
        COALESCE('#' || so.station_id::text,
                 station_normalize(origin_station),
                 origin_station) AS origin_key,
        COALESCE('#' || sd.station_id::text,
                 station_normalize(destination_station),
                 destination_station) AS destination_key,
        COALESCE(
            station_flag_prefix(origin_station) || station_display_name(
                so.curated_name, so.name_intl, so.name_local, so.names,
                :station_display, :user_lang
            ),
            origin_station
        ) AS origin_name,
        COALESCE(
            station_flag_prefix(destination_station) || station_display_name(
                sd.curated_name, sd.name_intl, sd.name_local, sd.names,
                :station_display, :user_lang
            ),
            destination_station
        ) AS destination_name,
        is_past, is_planned_future, trip_length, trip_duration, carbon, arrival_delay,
        departure_delay
    FROM time_categories t
    -- Filtering on resolved labels lets the joins use station_labels_resolved_key.
    LEFT JOIN station_labels lo
           ON lo.normalized   = station_normalize(t.origin_station)
          AND lo.station_type = station_type_bucket(t.trip_type)
          AND lo.station_id IS NOT NULL
    LEFT JOIN stations so ON so.station_id = lo.station_id
    LEFT JOIN station_labels ld
           ON ld.normalized   = station_normalize(t.destination_station)
          AND ld.station_type = station_type_bucket(t.trip_type)
          AND ld.station_id IS NOT NULL
    LEFT JOIN stations sd ON sd.station_id = ld.station_id
),
-- Ordered by key so a route and its return journey share a group.
ordered AS (
    SELECT
        LEAST(origin_key, destination_key) AS key_a,
        GREATEST(origin_key, destination_key) AS key_b,
        CASE WHEN origin_key <= destination_key THEN origin_name ELSE destination_name END
            AS name_a,
        CASE WHEN origin_key <= destination_key THEN destination_name ELSE origin_name END
            AS name_b,
        is_past, is_planned_future, trip_length, trip_duration, carbon, arrival_delay,
        departure_delay
    FROM resolved
)
SELECT
    jsonb_build_array(MAX(name_a), MAX(name_b))::text AS route,
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
FROM ordered
GROUP BY key_a, key_b
ORDER BY "count" DESC
-- Capped well below the old 10000: the page charts the top 10 and the
-- fullscreen view scrolls 20 rows at a time, so 1000 is ~50 screens of
-- depth. The tail was pure payload — it made a heavy user's stats response
-- several megabytes of rows nothing ever drew.
LIMIT 1000;
