"""Track heights for the 3D trip view, cleaned up from the ground under the path.

Each saved path that follows the ground stores the terrain's height at its points
(src/terrain.py, Mapterhorn) and, when a router drew it, its tunnel/bridge stretches
(GraphHopper's road_environment detail, or the same read off an OSRM router's steps, as
[start index, end index, kind] ranges). The ground's heights are right on open track but
not in a tunnel or on a bridge, which cross under or over it.

So, from those raw inputs:
  1. tunnel and bridge stretches close together are joined into one structure, and each
     is drawn as a straight line between its ends;
  2. a route that starts or ends inside a tunnel starts or ends at an underground
     station, set a fixed depth below the ground there (the terrain only knows the
     surface, and OSM does not record depths);
  3. spikes go (a running median over distance), then a moving average evens the rest;
  4. structures are straightened again between their now smoothed ends.
"""

import json
from bisect import bisect_left, bisect_right
from math import asin, cos, radians, sin, sqrt

from src.terrain import ground_heights

# Tunnel/bridge runs this close are one structure: a switch or crossover inside a tunnel
# splits the router's run where untagged track joins.
JOIN_GAP_M = 60
# Depth of an underground station a route starts or ends in.
UNDERGROUND_END_DEPTH_M = 15
# Width of the running median that removes single-tile spikes.
MEDIAN_WINDOW_M = 300
# Width of the moving average that evens out what is left.
AVERAGE_WINDOW_M = 150

STRUCTURES = ("tunnel", "bridge")


# Trips whose path follows the ground: they store its heights (src/terrain.py) and, when a
# router drew them, their tunnel/bridge stretches. Flights keep their recorded altitude.
TRACK_TYPES = ("train", "tram", "metro", "funicular", "rail", "bus", "car", "walk", "cycle",
               "scooter", "e_scooter", "ferry", "aerialway", "ski")


def track_env_from_details(details, point_count):
    """The router's tunnel/bridge stretches as a track_env JSON string, from the `details`
    that came with the path (`road_environment`: the rail router's, or what src/routing.py
    reads off an OSRM router's steps); None unless they fit the saved path: a path the
    user changed after routing (a freehand stretch, say) no longer has the router's
    points, and the ranges would land on the wrong places."""
    if not isinstance(details, dict) or not point_count:
        return None
    env = details.get("road_environment")
    # The router's ranges cover its whole path, so the last one ending on the last point
    # says the saved path still is that path.
    if not (isinstance(env, list) and env and isinstance(env[-1], list) and env[-1][1:2] == [point_count - 1]):
        return None
    ranges = []
    for item in env:
        if not (isinstance(item, list) and len(item) == 3):
            return None
        start, end, kind = item
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end < point_count):
            return None
        if kind in ("tunnel", "bridge", "ferry"):
            ranges.append([start, end, kind])
    return json.dumps(ranges)


def track_from_route(details, path):
    """(altitude, track_env) JSON strings to store with a path that follows the ground: the
    heights the ground has under it (src/terrain.py, the same data the 3D view draws), None
    when no terrain could be read, and the router's tunnel/bridge stretches, if any.
    path: the saved points, as {"lat", "lng"} or [lat, lng]."""
    points = [[p["lat"], p["lng"]] if isinstance(p, dict) else p for p in path]
    ground = ground_heights(points)
    altitude = json.dumps(ground) if any(v is not None for v in ground) else None
    return altitude, track_env_from_details(details, len(points))


def cumulative_distances(coords):
    """Metres along the path at each point; coords are [lat, lng] pairs."""
    dist = [0.0]
    for (la1, lo1), (la2, lo2) in zip(coords, coords[1:]):
        p1, p2 = radians(la1), radians(la2)
        h = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lo2 - lo1) / 2) ** 2
        dist.append(dist[-1] + 2 * 6371000 * asin(min(1.0, sqrt(h))))
    return dist


def structure_runs(env, dist):
    """[(kind, start, end)] tunnel/bridge runs from road_environment ranges, with runs of
    the same kind less than JOIN_GAP_M apart joined into one."""
    n = len(dist)
    runs = []
    for start, end, kind in sorted(
        (int(s), int(e), k) for s, e, k in (env or []) if k in STRUCTURES
    ):
        start, end = max(0, start), min(n - 1, end)
        if end <= start:
            continue
        if runs and runs[-1][0] == kind and dist[start] - dist[runs[-1][2]] < JOIN_GAP_M:
            runs[-1] = (kind, runs[-1][1], max(end, runs[-1][2]))
        else:
            runs.append((kind, start, end))
    return runs


def _straighten(z, dist, runs):
    for _, s, e in runs:
        span = dist[e] - dist[s]
        for i in range(s + 1, e):
            t = (dist[i] - dist[s]) / span if span > 0 else 0
            z[i] = z[s] + (z[e] - z[s]) * t


def fill_gaps(values):
    """Heights with None filled by interpolation along the index, or None if all missing."""
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        return None
    z = [float(v) if v is not None else None for v in values]
    for i in range(known[0]):
        z[i] = z[known[0]]
    for i in range(known[-1] + 1, len(z)):
        z[i] = z[known[-1]]
    for a, b in zip(known, known[1:]):
        for i in range(a + 1, b):
            z[i] = z[a] + (z[b] - z[a]) * (i - a) / (b - a)
    return z


def _running_median(z, dist, window):
    half = window / 2
    out = []
    for i, d in enumerate(dist):
        lo, hi = bisect_left(dist, d - half), bisect_right(dist, d + half)
        part = sorted(z[lo:hi])
        out.append(part[len(part) // 2])
    return out


def _moving_average(z, dist, window):
    half = window / 2
    out = []
    for d in dist:
        lo, hi = bisect_left(dist, d - half), bisect_right(dist, d + half)
        out.append(sum(z[lo:hi]) / (hi - lo))
    return out


def smooth_profile(coords, altitude, env):
    """Cleaned track heights (metres, one per point), or None without usable heights.

    coords: [[lat, lng], ...]; altitude: raw heights parallel to coords (None allowed);
    env: road_environment ranges [[start, end, kind], ...] on the same points."""
    if not coords or not altitude or len(altitude) != len(coords):
        return None
    z = fill_gaps(altitude)
    if z is None:
        return None
    dist = cumulative_distances(coords)
    runs = structure_runs(env, dist)

    last = len(z) - 1
    pinned = {}
    for kind, s, e in runs:
        if kind == "tunnel":
            if s == 0:
                pinned[0] = z[0] - UNDERGROUND_END_DEPTH_M
            if e == last:
                pinned[last] = z[last] - UNDERGROUND_END_DEPTH_M
    for i, v in pinned.items():
        z[i] = v
    _straighten(z, dist, runs)

    z = _running_median(z, dist, MEDIAN_WINDOW_M)
    z = _moving_average(z, dist, AVERAGE_WINDOW_M)
    # Smoothing pulls the route's ends towards their neighbours; an underground end
    # keeps its depth.
    for i, v in pinned.items():
        z[i] = v
    _straighten(z, dist, runs)
    return [round(v, 1) for v in z]
