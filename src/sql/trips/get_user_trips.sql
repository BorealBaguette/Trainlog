-- PostgreSQL. Returns a user's trips with past/plannedFuture/future flags and
-- aggregated tags. Output is adapted back to the legacy SQLite `trip` shape in
-- Python (adapt_pg_trip_row) so formatTrip/templates stay unchanged.
WITH base AS (
    SELECT trips.*,
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime
    FROM trips
    WHERE user_id = :user_id
      AND trip_type IN ('train', 'bus', 'air', 'helicopter', 'ferry')
),
trip_tags AS (
    SELECT ta.trip_id,
           json_agg(json_build_object('tag_id', ta.tag_id, 'name', t.name)) AS tags
    FROM tags_associations ta
    JOIN tags t ON ta.tag_id = t.uid
    GROUP BY ta.trip_id
)
SELECT
    base.*,
    airliners.iata,
    airliners.manufacturer,
    airliners.model,
    CASE
        WHEN NOT base.is_project
             AND (base.utc_filtered_start_datetime IS NULL
                  OR NOW() > base.utc_filtered_start_datetime)
        THEN 1 ELSE 0
    END AS past,
    CASE
        WHEN base.utc_filtered_start_datetime IS NOT NULL
             AND NOW() <= base.utc_filtered_start_datetime
        THEN 1 ELSE 0
    END AS "plannedFuture",
    CASE
        WHEN base.utc_filtered_start_datetime IS NULL AND base.is_project
        THEN 1 ELSE 0
    END AS future,
    trip_tags.tags AS tags
FROM base
LEFT JOIN airliners ON base.material_type = airliners.iata
LEFT JOIN trip_tags ON base.trip_id = trip_tags.trip_id
ORDER BY (base.utc_filtered_start_datetime IS NULL AND base.is_project) DESC,
         base.utc_filtered_start_datetime DESC NULLS LAST,
         base.trip_id DESC
