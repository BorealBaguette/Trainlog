import json
import math
import time

import polyline
from flask.wrappers import Response

from src.router_regions import all_in_region
from src.routing import forward_routing_core

# trip type -> (metres a GPS point may stray from the route, router snapping radius)
TOLERANCES = {
    "train": (150, 500),
    "metro": (150, 500),
    "tram": (100, 300),
    "funicular": (100, 300),
    "rail": (150, 500),
    "aerialway": (100, 300),
    "ferry": (1000, None),  # forward_routing_core sets its own ferry radiuses
    "bus": (50, 100),
    "car": (50, 100),
    "walk": (50, 100),
    "cycle": (50, 100),
    "scooter": (50, 100),
}
# Their waypoints can snap to a side road instead of the road travelled.
ROAD_TYPES = {"bus", "car", "walk", "cycle", "scooter"}
# Calls allowed to routing.openstreetmap.de per trip; our own routers take as many as needed.
MAX_CALLS_PUBLIC = 6
MAX_CALLS_OWN = 300

METRES_PER_DEGREE = 111195


class RouterUnavailable(Exception):
    pass


def clean_gps_route(raw_waypoints, trip_type="train"):
    """Snap a GPS trace to the network the way a car GPS recalculates: route to the
    destination, and each time the trace leaves the route, add a waypoint where it
    strayed furthest and re-route only that leg. Stretches off the mapped network (or
    unroutable) keep the raw GPS."""
    points = []
    for wp in raw_waypoints:
        p = (wp["lat"], wp["lng"])
        if not points or _haversine(points[-1], p) > 5:
            points.append(p)
    if len(points) < 2:
        return {"success": False, "error": "Need at least 2 waypoints"}

    tolerance, radius = TOLERANCES.get(trip_type, TOLERANCES["train"])
    # Cars go to our bus routers where there is one: bus is a superset of car.
    own_bus_router = trip_type in ("car", "bus") and any(
        all_in_region(region, raw_waypoints) for region in ("europe", "americas")
    )
    router = "bus" if own_bus_router else trip_type
    public = trip_type in ROAD_TYPES and not own_bus_router
    budget = MAX_CALLS_PUBLIC if public else MAX_CALLS_OWN
    calls = 0
    last_call = 0
    legs = {}
    # Points the router had to move further than the tolerance to reach the network,
    # e.g. driving inside a garage whose lanes aren't mapped.
    off_network = set()

    def call(coords, bearings):
        nonlocal calls, last_call
        calls += 1
        if public:
            # the volunteer-run public routers block IPs that send bursts
            time.sleep(max(0, last_call + 1 - time.monotonic()))
            last_call = time.monotonic()
        return _route(router, radius, coords, bearings)

    def route(stops):
        coords = [points[i] for i in stops]
        bearings = [_heading(points, i) if trip_type in ROAD_TYPES else None for i in stops]
        result = call(coords, bearings)
        if not result and any(b is not None for b in bearings) and calls < budget:
            result = call(coords, [None] * len(stops))
        if result:
            route_legs, moved = result
            legs.update(zip(zip(stops, stops[1:]), route_legs))
            off_network.update(i for i, m in zip(stops, moved) if m > tolerance)

    stops = [0, len(points) - 1]
    path = []
    routed = False
    k = 0
    try:
        while k < len(stops) - 1:
            a, b = stops[k], stops[k + 1]
            if (a, b) not in legs and calls < budget:
                route([a, b])
            leg = None if off_network & {a, b} else legs.get((a, b))

            if leg is None:
                stray = (a + b) // 2
            else:
                stray = _first_stray(leg, points[a : b + 1], tolerance)
                if stray is not None:
                    stray = a + _farthest_off(leg, points[a : b + 1], stray, tolerance)
                    if stray == b:
                        stray = b - 1

            # A short stretch the router can't follow (parking lot, garage) is best
            # drawn as recorded, so it isn't worth calls.
            short = sum(_haversine(p, q) for p, q in zip(points[a:b], points[a + 1 : b + 1])) < 4 * tolerance
            if stray is not None and a < stray < b and calls < budget and not short:
                stops.insert(k + 1, stray)
                route([a, stray, b])
                continue

            if leg is None or stray is not None:
                leg = points[a : b + 1]
            else:
                routed = True
            path.extend(leg[1:] if path else leg)
            k += 1
    except RouterUnavailable as e:
        return {"success": False, "error": f"Router unavailable: {e}"}

    if not routed:
        return {"success": False, "error": "No route found"}

    return {
        "success": True,
        "waypoints": [{"lat": points[i][0], "lng": points[i][1]} for i in stops],
        "path": [{"lat": lat, "lng": lng} for lat, lng in path],
        "distance": sum(_haversine(p, q) for p, q in zip(path, path[1:])),
        "reroute_count": len(stops) - 2,
        "compression_ratio": len(raw_waypoints) / len(path),
    }


def _route(router, radius, coords, bearings):
    """Route through coords [(lat, lng)]: one [(lat, lng)] leg per gap and how many metres
    each coord was moved to reach the network, or None when there is no route. An error
    from the router raises, as splitting the leg further would only hammer it."""
    profile = "ferry" if router == "ferry" else "driving"
    router_path = f"route/v1/{profile}/" + ";".join(f"{lng},{lat}" for lat, lng in coords)
    options = "overview=full"
    if radius:
        options += "&radiuses=" + ";".join([str(radius)] * len(coords))
    if any(b is not None for b in bearings):
        options += "&bearings=" + ";".join("" if b is None else f"{round(b) % 360},60" for b in bearings)
    result = forward_routing_core(
        routingType=router, path=router_path, flask_request=None, extra_args=options,
        use_new_router=True, public_fallback=False,
    )
    if isinstance(result, Response):
        result = result.get_data(as_text=True)
    try:
        if isinstance(result, str):
            result = json.loads(result)
    except ValueError:
        raise RouterUnavailable("unreadable response")
    if result.get("code") in ("NoRoute", "NoSegment"):
        return None
    if result.get("code") != "Ok":
        raise RouterUnavailable(result.get("message") or result.get("error") or result.get("code"))
    geometry = polyline.decode(result["routes"][0]["geometry"])
    if not geometry:
        return None

    snapped = result.get("waypoints") or []
    snapped = [(w["location"][1], w["location"][0]) for w in snapped] if len(snapped) == len(coords) else coords
    moved = [_haversine(c, s) for c, s in zip(coords, snapped)]
    if len(coords) == 2:
        return [geometry], moved
    via = snapped[1]
    split = min(range(len(geometry)), key=lambda i: _haversine(geometry[i], via))
    return [geometry[: split + 1], geometry[split:]], moved


def _heading(points, i):
    """Direction of travel at points[i], so the router snaps a waypoint to the road being
    driven rather than a closer side road. None at the ends and where the trace turns
    back, as a reversal has no single direction."""
    if i == 0 or i == len(points) - 1:
        return None
    before = next((points[j] for j in range(i - 1, -1, -1) if _haversine(points[j], points[i]) >= 30), points[0])
    after = next((points[j] for j in range(i + 1, len(points)) if _haversine(points[j], points[i]) >= 30), points[-1])
    turn = abs((_bearing(points[i], after) - _bearing(before, points[i]) + 180) % 360 - 180)
    return None if turn > 120 else _bearing(before, after)


def _bearing(p, q):
    lat1, lat2 = math.radians(p[0]), math.radians(q[0])
    dlng = math.radians(q[1] - p[1])
    x = math.sin(dlng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return math.degrees(math.atan2(x, y)) % 360


def _first_stray(leg, points, tolerance):
    """Index of the first point the leg fails to follow, or None. Points are matched in
    order, each only against the stretch of route reachable from the previous match, so
    a loop the route adds between two points counts as a stray just like a detour the
    route skips. A lone outlier is ignored."""
    cum = [0]
    for p, q in zip(leg, leg[1:]):
        cum.append(cum[-1] + _haversine(p, q))

    seg, pos, prev, miss = 0, 0, points[0], None
    for j, p in enumerate(points[1:], 1):
        reach = pos + 3 * _haversine(prev, p) + 2 * tolerance
        best_d, best_i, best_t = math.inf, seg, 0
        i = seg
        while i < len(leg) - 1 and cum[i] <= reach:
            d, t = _to_segment(p, leg[i], leg[i + 1])
            if d < best_d:
                best_d, best_i, best_t = d, i, t
            i += 1
        if best_d <= tolerance:
            seg, prev, miss = best_i, p, None
            pos = cum[seg] + best_t * (cum[seg + 1] - cum[seg])
        elif miss is None:
            miss = j
        else:
            return miss

    if miss is not None:
        return miss
    if cum[-1] - pos > 2 * tolerance:
        return len(points) - 1
    return None


def _farthest_off(leg, points, start, tolerance):
    """Index of the point furthest from the leg in the excursion beginning at start."""
    best, best_d = start, tolerance
    for k in range(start, len(points)):
        d = min((_to_segment(points[k], p, q)[0] for p, q in zip(leg, leg[1:])), default=math.inf)
        if d <= tolerance:
            break
        if d > best_d:
            best, best_d = k, d
    return best


def _to_segment(p, a, b):
    """Distance in metres from p to segment ab, and the position (0-1) of the closest point."""
    k = math.cos(math.radians(p[0]))
    ax, ay = (a[1] - p[1]) * k, a[0] - p[0]
    dx, dy = (b[1] - a[1]) * k, b[0] - a[0]
    length = dx * dx + dy * dy
    t = max(0, min(1, -(ax * dx + ay * dy) / length)) if length else 0
    return math.hypot(ax + t * dx, ay + t * dy) * METRES_PER_DEGREE, t


def _haversine(p, q):
    lat1, lat2 = math.radians(p[0]), math.radians(q[0])
    dlat, dlng = lat2 - lat1, math.radians(q[1] - p[1])
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))
