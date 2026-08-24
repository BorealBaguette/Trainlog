{base_filter}
{time_categories}

-- Endpoints are grouped through the station registry, not by their written text.
--
-- A station logged as "Gare de Lens" on one trip and "Lens" on another used to be two rows
-- here, each with a fraction of the trips. Resolving both spellings to the same station
-- collapses them into one group, named with whatever the registry says the station is called
-- — including an admin's curated_name and the reader's own naming preference. A label that
-- resolves to nothing is unchanged and still groups by itself, which is what keeps free text
-- ("Milly's house") working.
--
-- Resolution is a join, deliberately, and not the station_label_display()/station_label_key()
-- helpers that do the same thing in one call. Those hide a correlated subquery each, and at
-- two endpoints per trip they cost this query 7.7s for a 9,000-trip user against 84ms before
-- the registry existed. Joined once and read from the joined row, it stays in the same order
-- of magnitude as the original.
--
-- MATERIALIZED so station_normalize() — which calls unaccent — is evaluated once per endpoint
-- rather than again for the GROUP BY key when the CTE is inlined.
, endpoints AS MATERIALIZED (
    SELECT
        station_normalize(origin_station)  AS normalized,
        station_type_bucket(trip_type)     AS station_type,
        origin_station                     AS label,
        is_past, is_planned_future, trip_length, trip_duration, carbon, arrival_delay
    FROM time_categories
    UNION ALL
    SELECT
        station_normalize(destination_station),
        station_type_bucket(trip_type),
        destination_station,
        is_past, is_planned_future, trip_length, trip_duration, carbon, arrival_delay
    FROM time_categories
)
SELECT
    -- One name per group. Every row in a group resolves to the same station and so produces
    -- the same string; MAX is how that is stated to the planner, not a choice between rivals.
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
    SUM(COALESCE(arrival_delay, 0) * is_planned_future) AS "plannedFutureDelay"
FROM endpoints e
-- Only resolved labels matter here: an unresolved one contributes no station either way, and
-- they are the overwhelming majority of the table. The predicate lets this join read
-- station_labels_resolved_key (0060) instead of hashing every spelling ever typed.
LEFT JOIN station_labels sl
       ON sl.normalized = e.normalized
      AND sl.station_type = e.station_type
      AND sl.station_id IS NOT NULL
LEFT JOIN stations s ON s.station_id = sl.station_id
-- Two spellings of one station must collapse; two different stations that share a name must
-- not, which is why this keys on the station id and falls back to the spelling rather than
-- grouping on the displayed name.
GROUP BY COALESCE('#' || s.station_id::text, e.normalized, e.label)
ORDER BY count DESC
-- Capped well below the old 10000: the page charts the top 10 and the
-- fullscreen view scrolls 20 rows at a time, so 1000 is ~50 screens of
-- depth. The tail was pure payload — it made a heavy user's stats response
-- several megabytes of rows nothing ever drew.
LIMIT 1000;
