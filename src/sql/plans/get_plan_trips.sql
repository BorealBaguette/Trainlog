-- Plan-trips for a plan, with the path as GeoJSON (decoded with
-- geom_geojson_to_coords). Ordered by the planning order then day/time.
SELECT pt.*,
       ST_AsGeoJSON(pt.geom) AS geojson
FROM plan_trips pt
WHERE pt.plan_id = :plan_id
ORDER BY pt.sort_order,
         pt.start_day NULLS FIRST,
         pt.start_time NULLS FIRST,
         pt.uid
