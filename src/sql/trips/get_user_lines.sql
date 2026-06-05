-- PostgreSQL replacement for the legacy getUserLines. Returns each trip's route
-- as a JSON [[lat,lng],...] *string* (cast to text) so existing consumers that do
-- json.loads(row["path"]) keep working unchanged. PostGIS stores (lng lat); we
-- emit [lat, lng] to match the app.
SELECT
    p.trip_id,
    (
        SELECT json_agg(json_build_array(ST_Y(dp.geom), ST_X(dp.geom)) ORDER BY dp.path)
        FROM ST_DumpPoints(p.geom) AS dp
    )::text AS path
FROM paths p
WHERE p.trip_id = ANY(:ids)
