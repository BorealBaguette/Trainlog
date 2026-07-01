"""
Rebuild an over-sampled route into the compact form the current freehand editor
produces, so a legacy trip becomes indistinguishable from one hand-drawn today.

The old freehand endpoint stored the raw mouse track — thousands of ~20 m points.
The new editor (static/js/freehand.js) instead keeps only the user's waypoints and
rebuilds the drawn line from them (`buildSavePath`), adding geodesic curve points
only on long segments. This module ports that JS so an existing dense path can be:

  1. simplified to a handful of waypoints (Douglas-Peucker), then
  2. re-expanded with the exact same `buildSavePath` logic,

yielding (waypoints, path) that match what the editor would have saved. Keeping the
math identical to freehand.js is the whole point: the resulting trip reopens in the
freehand canvas exactly where the drawn line sits.
"""

import json
import math

from src.paths import coords_to_ewkt, geom_geojson_to_coords

# Mirror of the constants/geometry in static/js/freehand.js and
# static/js/lib/L.Geodesic.js so the rebuilt line matches the editor's output.
INTERP_THRESHOLD_M = 100000  # segments shorter than this stay straight (2 pts)
_EARTH_R = 6371071  # radius used by getDistanceFromLocations() in static/js/util.js


def _haversine(a, b):
    """Great-circle distance in metres between [lat, lng] points.

    Same formula/constant as getDistanceFromLocations() in the frontend, so the
    segment-length decisions here match what the editor made."""
    rlat1, rlat2 = math.radians(a[0]), math.radians(b[0])
    dlat = rlat2 - rlat1
    dlng = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    return 2 * _EARTH_R * math.asin(math.sqrt(h))


def _normalize_lng(lng):
    """Wrap a longitude into [-180, 180] (matches normalizeLng in freehand.js)."""
    return ((lng + 180) % 360 + 360) % 360 - 180


def _js_round(x):
    """JS Math.round: round half up (Python's round() is banker's rounding)."""
    return math.floor(x + 0.5)


def _spherical_midpoint(a, b):
    """Great-circle midpoint of two [lat, lng] points.

    Port of L.Geodesic's geom.midpoint (static/js/lib/L.Geodesic.js)."""
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2 = math.radians(b[0])
    dlng = math.radians(b[1] - a[1])
    bx = math.cos(lat2) * math.cos(dlng)
    by = math.cos(lat2) * math.sin(dlng)
    lat = math.atan2(
        math.sin(lat1) + math.sin(lat2),
        math.sqrt((math.cos(lat1) + bx) ** 2 + by ** 2),
    )
    lng = lng1 + math.atan2(by, math.cos(lat1) + bx)
    return [math.degrees(lat), math.degrees(lng)]


def _recursive_midpoint(a, b, steps):
    """Recursively subdivide a-b by great-circle midpoints (2**(steps+1)+1 points).

    Port of L.Geodesic's geom.recursiveMidpoint; returns the sub-line including
    both endpoints."""
    mid = _spherical_midpoint(a, b)
    if steps > 0:
        left = _recursive_midpoint(a, mid, steps - 1)
        right = _recursive_midpoint(mid, b, steps - 1)
        return left[:-1] + right
    return [a, mid, b]


def _geodesic_segment(a, b, steps):
    """Great-circle line a->b with the given subdivision level, longitudes left
    continuous (the editor uses wrap:false and normalises afterwards)."""
    return _recursive_midpoint(a, b, min(8, steps))


def build_save_path(points):
    """Port of buildSavePath() in static/js/freehand.js.

    `points` is the full waypoint list including endpoints ([[lat, lng], ...]).
    Keeps every waypoint; adds geodesic curve points only on segments longer than
    INTERP_THRESHOLD_M, with density scaled to length. Longitudes are normalised."""
    if not points:
        return []
    result = [points[0]]
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        dist = _haversine(a, b)
        if dist > INTERP_THRESHOLD_M:
            steps = min(8, max(1, _js_round(math.log2(dist / INTERP_THRESHOLD_M)) + 1))
            seg = _geodesic_segment(a, b, steps)
            result.extend(seg[1:])  # skip a (already added)
        else:
            result.append(b)
    return [[p[0], _normalize_lng(p[1])] for p in result]


def _perp_distance_m(p, a, b):
    """Perpendicular distance in metres from point p to segment a-b, using a local
    equirectangular projection (accurate for the short spans between path points)."""
    lat0 = math.radians((a[0] + b[0]) / 2)

    def xy(q):
        return (math.radians(q[1]) * math.cos(lat0) * _EARTH_R, math.radians(q[0]) * _EARTH_R)

    px, py = xy(p)
    ax, ay = xy(a)
    bx, by = xy(b)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _rdp(points, epsilon_m):
    """Iterative Douglas-Peucker simplification. Iterative (not recursive) to stay
    safe on the very long point lists the old freehand produced."""
    if len(points) < 3:
        return points[:]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        dmax, idx = 0.0, -1
        for i in range(start + 1, end):
            d = _perp_distance_m(points[i], points[start], points[end])
            if d > dmax:
                dmax, idx = d, i
        if dmax > epsilon_m and idx != -1:
            keep[idx] = True
            stack.append((start, idx))
            stack.append((idx, end))
    return [p for p, k in zip(points, keep) if k]


def transform_dense_path(coords, epsilon_m=500.0):
    """Turn a dense [[lat, lng], ...] path into the freehand editor's compact form.

    Returns (waypoints, path):
      - waypoints: intermediate user points only (no endpoints), as
        [{"lat":.., "lng":..}, ...] — exactly the shape stored in trips.waypoints.
      - path: the rebuilt [[lat, lng], ...] line (buildSavePath output) to store as
        the geometry.
    Endpoints are always preserved. Returns the path unchanged if it has < 3 points."""
    if not coords or len(coords) < 3:
        path = [[p[0], _normalize_lng(p[1])] for p in coords]
        return [], path

    simplified = _rdp(coords, epsilon_m)
    path = build_save_path(simplified)
    waypoints = [
        {"lat": p[0], "lng": _normalize_lng(p[1])} for p in simplified[1:-1]
    ]
    return waypoints, path


# ── DB orchestration ─────────────────────────────────────────────────────────
# These operate on an existing pg session so an endpoint can process several trips
# in one transaction. Ownership checks are left to the caller.

# How long a pre-conversion snapshot is kept (and thus how long a freehandify stays
# revertable). freehand_backup is meant to be a short-lived undo buffer, not a
# permanent archive, so snapshots past this age are purged opportunistically on every
# run (and by the admin cleanup endpoint).
BACKUP_TTL_DAYS = 7


def purge_expired_backups(pg, days=BACKUP_TTL_DAYS):
    """Delete snapshots older than `days`. Returns the number of rows removed."""
    return pg.execute(
        "DELETE FROM freehand_backup WHERE created < NOW() - make_interval(days => :d)",
        {"d": days},
    ).rowcount

def _geom_coords(pg, table, trip_id):
    """[[lat, lng], ...] of the geometry in `table` for trip_id, or [] if none."""
    row = pg.execute(
        f"SELECT ST_AsGeoJSON(geom) AS g FROM {table} WHERE trip_id = :t",
        {"t": trip_id},
    ).fetchone()
    return geom_geojson_to_coords(row["g"]) if row and row["g"] else []


def apply_to_trip(pg, trip_id, epsilon_m=50.0):
    """Simplify one trip's route into the freehand editor's compact form.

    The first run snapshots the trip's current route into freehand_backup; every run
    (first or repeat) rebuilds from that snapshot, so changing epsilon always works
    from the full-detail original rather than compounding a previous simplification.
    Writes the rebuilt geometry, the intermediate waypoints, and route_source
    'freehand'. Returns a status dict; never raises for expected skips."""
    backup = _geom_coords(pg, "freehand_backup", trip_id)
    if backup:
        source = backup
    else:
        source = _geom_coords(pg, "paths", trip_id)
        if not source:
            return {"trip_id": trip_id, "status": "skipped", "reason": "no path"}
        # Snapshot the untouched original before the first simplification.
        cur = pg.execute(
            "SELECT waypoints, route_source FROM trips WHERE trip_id = :t",
            {"t": trip_id},
        ).fetchone()
        pg.execute(
            "INSERT INTO freehand_backup (trip_id, geom, waypoints, route_source)"
            " SELECT trip_id, geom, :w, :rs FROM paths WHERE trip_id = :t",
            {"t": trip_id, "w": cur["waypoints"], "rs": cur["route_source"]},
        )

    if len(source) < 3:
        return {"trip_id": trip_id, "status": "skipped", "reason": "too few points"}

    waypoints, path = transform_dense_path(source, epsilon_m)
    ewkt = coords_to_ewkt(path)
    if ewkt is None:
        return {"trip_id": trip_id, "status": "skipped", "reason": "empty rebuilt path"}

    pg.execute(
        "UPDATE trips SET waypoints = :w, route_source = 'freehand' WHERE trip_id = :t",
        {"t": trip_id, "w": json.dumps(waypoints)},
    )
    pg.execute(
        "INSERT INTO paths (trip_id, geom) VALUES (:t, ST_GeomFromEWKT(:e))"
        " ON CONFLICT (trip_id) DO UPDATE SET geom = EXCLUDED.geom",
        {"t": trip_id, "e": ewkt},
    )
    return {
        "trip_id": trip_id,
        "status": "converted",
        "original_points": len(source),
        "waypoints": len(waypoints),
        "path_points": len(path),
    }


def revert_trip(pg, trip_id):
    """Restore a trip's original route from freehand_backup and drop the snapshot.

    No-op (returns a skip) if the trip was never freehandified."""
    row = pg.execute(
        "SELECT waypoints, route_source FROM freehand_backup WHERE trip_id = :t",
        {"t": trip_id},
    ).fetchone()
    if row is None:
        return {"trip_id": trip_id, "status": "skipped", "reason": "no backup"}

    pg.execute(
        "UPDATE paths SET geom = b.geom FROM freehand_backup b"
        " WHERE paths.trip_id = b.trip_id AND paths.trip_id = :t",
        {"t": trip_id},
    )
    pg.execute(
        "UPDATE trips SET waypoints = :w, route_source = :rs WHERE trip_id = :t",
        {"t": trip_id, "w": row["waypoints"], "rs": row["route_source"]},
    )
    pg.execute("DELETE FROM freehand_backup WHERE trip_id = :t", {"t": trip_id})
    return {"trip_id": trip_id, "status": "reverted"}
