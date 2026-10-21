"""The stations of a user's trips, for the stations map: each trip end as its trip names it,
at its path's first or last point, with how many times the user has been there."""

import math

from src.pg import pg_session

# Stations of one trip type nearer than this are one, whatever their names (the two sides of a stop).
MERGE_RADIUS_M = 25

# Ends of one name and trip type are one station, at their average point; planned trips are left out.
STATIONS_SQL = """
SELECT t.trip_type, e.label,
       avg(ST_Y(e.point))::float8 AS lat,
       avg(ST_X(e.point))::float8 AS lng,
       count(*) AS uses
FROM trips t
JOIN paths p ON p.trip_id = t.trip_id
CROSS JOIN LATERAL (VALUES
    (t.origin_station, ST_StartPoint(ST_GeometryN(p.geom, 1))),
    (t.destination_station, ST_EndPoint(ST_GeometryN(p.geom, ST_NumGeometries(p.geom))))
) AS e(label, point)
WHERE t.user_id = :user_id AND e.point IS NOT NULL
  AND NOT t.is_project AND (t.utc_start_datetime IS NULL OR t.utc_start_datetime <= now())
GROUP BY 1, 2
"""


def _metres(a, b):
    x = math.radians(b.lng - a.lng) * math.cos(math.radians((a.lat + b.lat) / 2))
    y = math.radians(b.lat - a.lat)
    return 6371000 * math.hypot(x, y)


def _merge_nearby(rows):
    """The rows in groups of one trip type each within MERGE_RADIUS_M of another of the group."""
    parent = list(range(len(rows)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Cells of about the radius, so only rows in neighbouring cells are compared.
    cell = MERGE_RADIUS_M / 111000
    grid = {}
    for i, row in enumerate(rows):
        cx, cy = int(row.lng // cell), int(row.lat // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((row.trip_type, cx + dx, cy + dy), []):
                    if _metres(row, rows[j]) <= MERGE_RADIUS_M:
                        parent[root(i)] = root(j)
        grid.setdefault((row.trip_type, cx, cy), []).append(i)

    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(root(i), []).append(row)
    return groups.values()


def user_stations(user_id):
    """The user's stations as a GeoJSON FeatureCollection of points with name, type and uses."""
    with pg_session() as pg:
        rows = pg.execute(STATIONS_SQL, {"user_id": user_id}).fetchall()

    features = []
    for group in _merge_nearby(rows):
        group.sort(key=lambda row: -row.uses)
        uses = sum(row.uses for row in group)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [
                sum(row.lng * row.uses for row in group) / uses,
                sum(row.lat * row.uses for row in group) / uses,
            ]},
            "properties": {
                "name": group[0].label or "",
                "names": [row.label or "" for row in group],
                "type": group[0].trip_type,
                "uses": uses,
            },
        })
    return {"type": "FeatureCollection", "features": features}
