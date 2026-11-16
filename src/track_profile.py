"""Track heights for the 3D trip view, cleaned up from what the rail router returns.

The router (OpenRailRouting with Skadi elevation) gives one height per path point,
sampled from ~30 m terrain tiles, plus the tunnel/bridge stretches of the route
(GraphHopper's road_environment detail, as [start index, end index, kind] ranges). Its
heights are right overall but noisy on open track: a line on a ledge cut into a steep
valley side samples the slope around it. It also straightens a tunnel only between the
points where other track joins, so a tunnel with a switch inside comes out bent.

So, from those raw inputs:
  1. tunnel and bridge stretches close together are joined into one structure, and each
     is drawn as a straight line between its ends;
  2. a route that starts or ends inside a tunnel starts or ends at an underground
     station, set a fixed depth below the ground there (the terrain only knows the
     surface, and OSM does not record depths);
  3. spikes go (a running median over distance), then the profile is held to the
     steepest gradient trains of that kind climb, as the midpoint of the tightest
     profiles that never climb faster than that above and below the heights;
  4. structures are straightened again between their now smoothed ends.
"""

import json
from bisect import bisect_left, bisect_right
from math import asin, cos, radians, sin, sqrt

# Steepest sustained gradient per trip type: adhesion main lines stay under ~4 %, the
# steepest (Flåmsbana 5.5 %, Bernina 7 %) set the cap. None = no cap (cable-hauled).
MAX_GRADE = {"train": 0.07, "rail": 0.07, "metro": 0.08, "tram": 0.10, "funicular": None}
# Tunnel/bridge runs this close are one structure: a switch or crossover inside a tunnel
# splits the router's run where untagged track joins.
JOIN_GAP_M = 60
# Depth of an underground station a route starts or ends in.
UNDERGROUND_END_DEPTH_M = 15
# Width of the running median that removes single-tile spikes.
MEDIAN_WINDOW_M = 300

STRUCTURES = ("tunnel", "bridge")


def track_from_details(details, point_count):
    """(altitude, track_env) JSON strings to store with a path the rail router drew, from
    the `details` that came with it: `elevation` (one raw height per point, see
    src/graphhopper.py) and `road_environment`. Each is None unless it fits the saved
    path: a path the user changed after routing (a freehand stretch, say) no longer has
    the router's points, and its heights and ranges would land on the wrong places."""
    if not isinstance(details, dict) or not point_count:
        return None, None
    elevation = details.get("elevation")
    altitude = None
    if (
        isinstance(elevation, list)
        and len(elevation) == point_count
        and all(isinstance(v, (int, float)) or v is None for v in elevation)
        and any(v is not None for v in elevation)
    ):
        altitude = json.dumps(elevation)
    env = details.get("road_environment")
    track_env = None
    # The router's ranges cover its whole path, so the last one ending on the last point
    # says the saved path still is that path.
    if isinstance(env, list) and env and isinstance(env[-1], list) and env[-1][1:2] == [point_count - 1]:
        ranges = []
        for item in env:
            if not (isinstance(item, list) and len(item) == 3):
                return altitude, None
            start, end, kind = item
            if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end < point_count):
                return altitude, None
            if kind in ("tunnel", "bridge", "ferry"):
                ranges.append([start, end, kind])
        track_env = json.dumps(ranges)
    return altitude, track_env


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


def _fill_gaps(values):
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


def _grade_limited(z, dist, grade):
    """Midpoint of the lowest profile never steeper than `grade` that stays above z and
    the highest that stays below it. Both bounds hold the gradient, so their mean does."""
    n = len(z)
    upper, lower = list(z), list(z)
    for i in range(1, n):
        step = grade * (dist[i] - dist[i - 1])
        upper[i] = max(upper[i], upper[i - 1] - step)
        lower[i] = min(lower[i], lower[i - 1] + step)
    for i in range(n - 2, -1, -1):
        step = grade * (dist[i + 1] - dist[i])
        upper[i] = max(upper[i], upper[i + 1] - step)
        lower[i] = min(lower[i], lower[i + 1] + step)
    return [(u + l) / 2 for u, l in zip(upper, lower)]


def smooth_profile(coords, altitude, env, trip_type):
    """Cleaned track heights (metres, one per point), or None without usable heights.

    coords: [[lat, lng], ...]; altitude: raw heights parallel to coords (None allowed);
    env: road_environment ranges [[start, end, kind], ...] on the same points."""
    if not coords or not altitude or len(altitude) != len(coords):
        return None
    z = _fill_gaps(altitude)
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
    grade = MAX_GRADE.get(trip_type, MAX_GRADE["train"])
    if grade:
        z = _grade_limited(z, dist, grade)
    # Smoothing pulls the route's ends towards their neighbours; an underground end
    # keeps its depth.
    for i, v in pinned.items():
        z[i] = v
    _straighten(z, dist, runs)
    return [round(v, 1) for v in z]
