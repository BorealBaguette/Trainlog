-- Where the trips using a label start or end: the per-axis median of their path endpoints,
-- which ignores the odd mis-picked or reused label. spread_m, the median distance from that
-- centre, shows whether the label is used for one place or several.
WITH pts AS (
    -- A POINT path is a trip with no route drawn; the point is both ends.
    SELECT CASE WHEN GeometryType(p.geom) = 'POINT' THEN p.geom
                ELSE ST_StartPoint(ST_GeometryN(p.geom, 1)) END AS pt,
           t.username
    FROM trips t
    JOIN paths p ON p.trip_id = t.trip_id
    WHERE station_normalize(t.origin_station) = station_normalize(:label)
      AND station_type_bucket(t.trip_type) = :station_type
    UNION ALL
    SELECT CASE WHEN GeometryType(p.geom) = 'POINT' THEN p.geom
                ELSE ST_EndPoint(ST_GeometryN(p.geom, ST_NumGeometries(p.geom))) END,
           t.username
    FROM trips t
    JOIN paths p ON p.trip_id = t.trip_id
    WHERE station_normalize(t.destination_station) = station_normalize(:label)
      AND station_type_bucket(t.trip_type) = :station_type
),
valid AS (
    SELECT pt, username FROM pts WHERE pt IS NOT NULL AND NOT ST_IsEmpty(pt)
),
centre AS (
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY ST_Y(pt)) AS lat,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ST_X(pt)) AS lng,
           count(*)                                              AS points,
           count(DISTINCT username)                              AS users
    FROM valid
)
SELECT c.lat,
       c.lng,
       c.points,
       c.users,
       (SELECT percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY ST_Distance(
                       v.pt::geography,
                       ST_SetSRID(ST_MakePoint(c.lng, c.lat), 4326)::geography))
        FROM valid v) AS spread_m
FROM centre c
WHERE c.points > 0;
