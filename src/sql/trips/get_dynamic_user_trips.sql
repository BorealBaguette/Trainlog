-- PostgreSQL. Builds the FilteredTrips CTE consumed by the dynamic-trips
-- endpoint, which appends "SELECT COUNT(*)/SELECT * FROM FilteredTrips [WHERE ...]
-- [ORDER BY ...] [LIMIT/OFFSET]". Columns are aliased to the legacy SQLite `trip`
-- names (uid, type, purchasing_date) so the appended filters/sorts keep working;
-- rows are adapted back to the legacy shape in Python (adapt_pg_trip_row).
WITH base AS (
    SELECT
        trip_id AS uid,
        user_id,
        origin_station,
        destination_station,
        start_datetime,
        end_datetime,
        is_project,
        utc_start_datetime,
        utc_end_datetime,
        estimated_trip_duration,
        manual_trip_duration,
        trip_length,
        operator,
        countries,
        line_name,
        created,
        last_modified,
        trip_type AS type,
        material_type,
        material_type_advanced,
        seat,
        reg,
        waypoints,
        notes,
        price,
        currency,
        ticket_id,
        purchase_date AS purchasing_date,
        carbon,
        visibility,
        departure_delay,
        arrival_delay,
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime
    FROM trips
    WHERE user_id = :user_id
),
sub AS (
    SELECT
        base.*,
        CASE
            WHEN base.utc_filtered_start_datetime IS NOT NULL
                 AND base.utc_filtered_end_datetime IS NOT NULL
                 AND base.utc_filtered_start_datetime <> base.utc_filtered_end_datetime
            THEN EXTRACT(EPOCH FROM (base.utc_filtered_end_datetime - base.utc_filtered_start_datetime))
            ELSE COALESCE(base.manual_trip_duration, base.estimated_trip_duration)
        END AS trip_duration_seconds,
        o.short_name AS operator_name,
        base.start_datetime::time AS start_time,
        base.end_datetime::time AS end_time,
        (SELECT l.logo_url
         FROM operator_logos l
         WHERE l.operator_id = o.operator_id
           AND (l.effective_date <= base.utc_filtered_start_datetime
                OR l.effective_date IS NULL
                OR base.utc_filtered_start_datetime IS NULL)
         ORDER BY l.effective_date DESC NULLS LAST, l.uid DESC
         LIMIT 1) AS logo_url
    FROM base
    LEFT JOIN operators o ON o.short_name = TRIM(split_part(base.operator, ',', 1))
),
trip_tags AS (
    SELECT ta.trip_id,
           json_agg(json_build_object('tag_id', ta.tag_id, 'name', t.name)) AS tags
    FROM tags_associations ta
    JOIN tags t ON ta.tag_id = t.uid
    GROUP BY ta.trip_id
),
FilteredTrips AS (
    SELECT
        sub.*,
        airliners.iata,
        airliners.manufacturer,
        airliners.model,
        sub.trip_length / NULLIF(sub.trip_duration_seconds, 0) AS trip_speed,
        CASE
            WHEN NOT sub.is_project
                 AND (sub.utc_filtered_start_datetime IS NULL OR NOW() > sub.utc_filtered_start_datetime)
            THEN 1 ELSE 0
        END AS past,
        CASE
            WHEN sub.utc_filtered_start_datetime IS NOT NULL AND NOW() <= sub.utc_filtered_start_datetime
            THEN 1 ELSE 0
        END AS "plannedFuture",
        CASE
            WHEN sub.utc_filtered_start_datetime IS NULL AND sub.is_project
            THEN 1 ELSE 0
        END AS future,
        trip_tags.tags AS tags
    FROM sub
    LEFT JOIN airliners ON sub.material_type = airliners.iata
    LEFT JOIN tickets ON sub.ticket_id = tickets.uid
    LEFT JOIN trip_tags ON sub.uid = trip_tags.trip_id
    WHERE
        (CASE
            WHEN NOT sub.is_project
                 AND (sub.utc_filtered_start_datetime IS NULL OR NOW() > sub.utc_filtered_start_datetime)
            THEN 1 ELSE 0
        END) = :past
        AND (
            remove_diacritics(LOWER(origin_station)) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(destination_station)) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(operator, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(countries, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(line_name, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(CAST(sub.start_datetime AS text), ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(CAST(sub.end_datetime AS text), ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(sub.type)) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(sub.notes, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(sub.reg, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(material_type, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(material_type_advanced, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(airliners.iata, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(airliners.manufacturer, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(airliners.model, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            remove_diacritics(LOWER(COALESCE(tickets.name, ''))) LIKE remove_diacritics(LOWER(:search)) OR
            EXISTS (
                SELECT 1 FROM tags_associations fta
                JOIN tags ft ON fta.tag_id = ft.uid
                WHERE fta.trip_id = sub.uid
                  AND remove_diacritics(LOWER(ft.name)) LIKE remove_diacritics(LOWER(:search))
            )
        )
)
