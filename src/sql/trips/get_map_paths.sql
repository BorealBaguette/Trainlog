-- Path geometry for a batch of the user's trips (new_map.html), as Google
-- encoded polylines at 1e-6 precision (~0.1 m — zoomed-in precision is a core
-- feature, so no simplification). Several times smaller than GeoJSON on the wire
-- and in the device cache. Same visibility rules as get_map_trips, so a public
-- viewer can't pull private paths by guessing ids.
-- :overview = 1 returns the zoomed-out version instead: simplified to ~22 m
-- (2e-4 deg, under a pixel below zoom 12, where the map switches to full
-- paths) at 1e-5 precision. For the heaviest account that is ~1M points
-- instead of ~9M. preserveCollapsed keeps short paths from vanishing.
-- ST_AsEncodedPolyline only takes LINESTRING / MULTIPOINT, so anything else
-- (1-point paths) is passed as its points; the client skips < 2 points.
SELECT
    t.trip_id AS uid,
    (EXTRACT(EPOCH FROM p.geom_updated) * 1000)::bigint AS path_version,
    CASE WHEN :overview = 1
        THEN ST_AsEncodedPolyline(ST_Simplify(g.line, 0.0002, true), 5)
        ELSE ST_AsEncodedPolyline(g.line, 6)
    END AS polyline
FROM trips t
JOIN paths p ON p.trip_id = t.trip_id
CROSS JOIN LATERAL (
    SELECT CASE WHEN GeometryType(p.geom) = 'LINESTRING' THEN p.geom ELSE ST_Points(p.geom) END AS line
) g
WHERE t.user_id = :user_id
  AND t.trip_id = ANY(:trip_ids)
  AND (
      :public = 0
      OR (:public = 1 AND t.visibility = 'public')
      OR (:friend = 1 AND t.visibility = 'friends')
      OR (t.visibility IS NULL AND t.trip_type IN ('train', 'air', 'bus', 'ferry', 'aerialway', 'tram', 'metro'))
  )
