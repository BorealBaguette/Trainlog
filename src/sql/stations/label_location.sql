-- Where the trips using a label actually start or end.
--
-- A written label carries no coordinates, so registering one against a search result was a
-- guess: a search for "Roermond" offers two results with the same name and nothing to choose
-- between them, and picking the wrong one attaches hundreds of trips to a place they never
-- went. The trips themselves know the answer — their path begins at the origin and ends at
-- the destination — so this recovers it.
--
-- The median, not the mean. Coordinates come from whatever the client sent when each trip was
-- drawn, so a handful are far away: a mis-picked autocomplete result, a freehand path drawn
-- roughly, a trip that reuses the spelling for somewhere else entirely. A mean is dragged by
-- those; a median ignores them until they are half the data. Taken per axis, which is not the
-- true geometric median but is the robust summary this needs and is one pass in SQL.
--
-- `spread_m` is the median distance from that centre, and it is the honest part of the answer:
-- a label used consistently for one station gives a few hundred metres, while one used for two
-- different places gives tens of kilometres and means the centre describes neither. The UI
-- shows it so an admin can distrust a match rather than be told a confident wrong number.
WITH pts AS (
    -- The path's first point is the origin, its last is the destination. A path stored as a
    -- POINT is a trip with no route drawn; the point is both ends of it.
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
       -- Cast to geography so this is metres on the sphere rather than degrees.
       (SELECT percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY ST_Distance(
                       v.pt::geography,
                       ST_SetSRID(ST_MakePoint(c.lng, c.lat), 4326)::geography))
        FROM valid v) AS spread_m
FROM centre c
WHERE c.points > 0;
