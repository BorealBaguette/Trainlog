-- Plan-trips for a plan, with the path as GeoJSON (decoded with
-- geom_geojson_to_coords) and the operator's logo (latest era — plan dates are
-- tentative). Ordered by the planning order then day/time.
SELECT pt.*,
       ST_AsGeoJSON(pt.geom) AS geojson,
       (SELECT l.logo_url
        FROM operator_logos l
        WHERE l.operator_id = o.operator_id
        ORDER BY l.effective_date DESC NULLS LAST, l.uid DESC
        LIMIT 1) AS logo_url
FROM plan_trips pt
-- short_name is not unique; pick one operator deterministically (mirrors get_trip.sql).
LEFT JOIN LATERAL (
    SELECT operator_id
    FROM operators
    WHERE short_name = TRIM(split_part(pt.operator, ',', 1))
    ORDER BY operator_id
    LIMIT 1
) o ON TRUE
WHERE pt.plan_id = :plan_id
ORDER BY pt.sort_order,
         pt.start_day NULLS FIRST,
         pt.start_time NULLS FIRST,
         pt.uid
