# src/routing.py
import os

import requests
from urllib.parse import parse_qs
from flask import make_response

# Import these from wherever they currently live in your project
# Adjust imports to match your structure.
from src.router_regions import all_in_region
from src.graphhopper import convert_graphhopper_to_osrm, decode_polyline

# The new (GraphHopper) rail router; a test instance can stand in through TRAIN_GH_URL.
TRAIN_GH_URL = os.environ.get("TRAIN_GH_URL", "https://train-gh.srv.trainlog.me").rstrip("/")
# road_environment: where the route runs in a tunnel or over a bridge, kept with the trip
# (src/track_profile.py).
GH_DETAILS = ["electrified", "distance", "time", "road_environment"]


# Track filters of the new router's train profile, sent by routing.js as plain
# parameters and turned into custom_model conditions here, so clients never write
# conditions themselves. Untagged track passes every filter on purpose.
FILTER_KEYS = ("avoid_highspeed", "max_speed", "electrified", "power", "gauge")
POWER_SYSTEMS = {
    "25kv": "voltage >= 24000 && voltage <= 26000 && frequency >= 47.5 && frequency <= 52.5",
    "15kv": "voltage >= 14000 && voltage <= 16000 && frequency >= 15 && frequency <= 17.5",
    "3kv": "voltage >= 2900 && voltage <= 3100 && frequency == 0",
    "1.5kv": "voltage >= 1400 && voltage <= 1600 && frequency == 0",
    "750v": "voltage >= 700 && voltage <= 800 && frequency == 0",
}
# A gauge choice takes any track within GAUGE_TOLERANCE mm of it, so 1520 and 1524 run
# together (trains cross between Finland and Russia) while 1668 and 1676 stay apart.
# Narrow gauges are one choice, any track of 1067 mm or less. Kept in step with
# ROUTE_GAUGES in routing.js.
GAUGES = ("1676", "1668", "1600", "1524", "1520", "1435")
GAUGE_TOLERANCE = 5
NARROW_GAUGE_MAX = 1067


def filter_conditions(query):
    """The conditions of the tracks to exclude, one per active filter, and the train's
    top speed (0 when uncapped)."""
    get = lambda key: query.get(key, [""])[0]
    conditions = []
    # max_speed only caps the train's speed; high-speed lines have their own toggle.
    max_speed = int(get("max_speed")) if get("max_speed").isdigit() else 0
    if get("avoid_highspeed") == "1":
        conditions.append("highspeed")
    power = [POWER_SYSTEMS[p] for p in get("power").split(",") if p in POWER_SYSTEMS]
    if get("electrified") == "yes" or power:
        conditions.append("electrified == NO")
    if power:
        conditions.append("!(" + " || ".join(f"({p})" for p in power) + " || voltage == 0)")
    if get("gauge") == "narrow":
        conditions.append(f"gauge > {NARROW_GAUGE_MAX}")
    elif get("gauge") in GAUGES:
        gauge = int(get("gauge"))
        conditions.append(f"gauge != 0 && (gauge < {gauge - GAUGE_TOLERANCE} || gauge > {gauge + GAUGE_TOLERANCE})")
    return conditions, max_speed


def with_road_environment(data, args):
    """An OSRM answer with its tunnels as details.road_environment, the shape the rail router
    gives ([start, end, kind] over the path's points, end shared with the next start), so a
    saved trip keeps them (src/track_profile.py). OSRM tags the road leaving each junction
    of its steps with classes, "tunnel" among them; those junctions are placed on the route's
    points in order. Left as it is without steps or geometry."""
    def decode(geometry):
        if isinstance(geometry, dict):
            return [(lat, lng) for lng, lat in geometry["coordinates"]]
        return decode_polyline(geometry, precision=6 if "polyline6" in args else 5)

    try:
        route = data["routes"][0]
        steps = [step for leg in route["legs"] for step in leg["steps"]]
        # The points the client draws and saves: the route's overview when it asked for one,
        # else (the routing machine's overview=false) every step's geometry end to end,
        # repeated points included, as it joins them
        if route.get("geometry"):
            points = decode(route["geometry"])
        else:
            points = [p for step in steps for p in decode(step["geometry"])]
        junctions = [(it["location"], "tunnel" in it.get("classes", []))
                     for step in steps for it in step.get("intersections", [])]
    except (KeyError, IndexError, TypeError, ValueError):
        return data
    n = len(points)
    if n < 2 or not junctions:
        return data
    kinds = ["road"] * (n - 1)
    at = 0
    placed = []
    for (lng, lat), tunnel in junctions:
        window = range(at, min(n, at + 500))
        at = min(window, key=lambda i: (points[i][0] - lat) ** 2 + ((points[i][1] - lng) * 0.5) ** 2)
        placed.append((at, tunnel))
    for (i, tunnel), (j, _) in zip(placed, placed[1:] + [(n - 1, False)]):
        if tunnel:
            for k in range(i, j):
                kinds[k] = "tunnel"
    ranges = []
    for k, kind in enumerate(kinds):
        if ranges and ranges[-1][2] == kind:
            ranges[-1][1] = k + 1
        else:
            ranges.append([k, k + 1, kind])
    route.setdefault("details", {})["road_environment"] = ranges
    return data


def forward_routing_core(routingType, path, flask_request, extra_args=None, use_new_router=None, public_fallback=True):
    """use_new_router None reads it from the request. public_fallback False keeps bus
    routing on its own router, answering NoRoute rather than asking a public one."""
    # GraphHopper profile matching the original (pre-normalization) trip type
    gh_profile = {"train": "train", "tram": "tram", "metro": "metro"}.get(routingType, "all")

    # Normalize routing type
    if routingType in ("train", "tram", "metro", "funicular", "rail"):
        routingType = "train"
    elif routingType == "scooter":
        routingType = "cycle"

    radiuses = None

    # Determine base URL + (optional) return_code for bus
    return_code = None

    if routingType == "train":
        if use_new_router is None:
            use_new_router = flask_request.args.get("use_new_router", "false").lower() == "true"
        base = TRAIN_GH_URL if use_new_router else "https://train.srv.trainlog.me"

    elif routingType == "ferry":
        base = "https://ferry.srv.trainlog.me"
        coord_pairs = [
            {"lng": float(coord.split(",")[0]), "lat": float(coord.split(",")[1])}
            for coord in path.replace("route/v1/ferry/", "").split(";")
        ]
        radiuses = ";".join(["10000"] * len(coord_pairs))

    elif routingType == "aerialway":
        base = "https://aerialway.srv.trainlog.me"
        
    elif routingType == "ski":
        base = "https://ski.srv.trainlog.me"

    elif routingType == "car":
        base = "https://routing.openstreetmap.de/routed-car"

    elif routingType == "walk":
        base = "https://routing.openstreetmap.de/routed-foot"

    elif routingType == "cycle":
        base = "https://routing.openstreetmap.de/routed-bike"

    elif routingType == "bus":
        routers = {
            "trainlog": ("https://bus.srv.trainlog.me", 231),
            "jkimb": ("https://busrouter.jkimball.dev", 233),
            "fallback": ("https://routing.openstreetmap.de/routed-car", 234),
        }

        coord_pairs = [
            {"lng": float(coord.split(",")[0]), "lat": float(coord.split(",")[1])}
            for coord in path.replace("route/v1/driving/", "").split(";")
        ]

        base, return_code = routers["fallback"]
        for region, router in (("europe", "trainlog"), ("americas", "jkimb")):
            if all_in_region(region, coord_pairs):
                base, return_code = routers[router]
                break

    else:
        # Optional: make unknown routing types explicit
        return make_response({"error": f"Unsupported routingType: {routingType}"}, 400)

    # Build args from extra_args or incoming request
    if extra_args is not None:
        args = extra_args
    else:
        args = flask_request.query_string.decode("utf-8") if flask_request.query_string else ""
    # remove use_new_router=true from forwarded query string
    args = (
        args.replace("&use_new_router=true", "")
            .replace("use_new_router=true&", "")
            .replace("use_new_router=true", "")
    ).strip("&")

    # Let the client override the GraphHopper profile (train/tram/metro/all)
    requested_profile = parse_qs(args).get("profile", [None])[0]
    if requested_profile:
        gh_profile = requested_profile
        args = "&".join(p for p in args.split("&") if not p.startswith("profile="))

    # Per-waypoint hard/soft modes, only understood by the new (GraphHopper) router:
    # one entry per coordinate, sent as a single parameter (it drops repeated ones).
    # Never forwarded to the old router, which may reject unknown parameters.
    waypoint_modes = parse_qs(args).get("waypoint_modes", [None])[0]
    args = "&".join(p for p in args.split("&") if not p.startswith("waypoint_modes="))
    if waypoint_modes:
        modes = [m.strip().lower() or "soft" for m in waypoint_modes.split(",")]
        point_count = len(path.split("/")[-1].split(";"))
        if len(modes) != point_count or not set(modes) <= {"hard", "soft"}:
            waypoint_modes = None  # malformed: fall back to the router default (all soft)
        else:
            waypoint_modes = ",".join(modes)

    # Only the new router's train profile takes filters (others answer 400), and the
    # old router never sees them.
    filters, max_speed = filter_conditions(parse_qs(args)) if use_new_router and gh_profile == "train" else ([], 0)
    args = "&".join(p for p in args.split("&") if p.split("=")[0] not in FILTER_KEYS)

    def build_url(base_url):
        q = f"?{args}" if args else ""
        full_url = f"{base_url}/{path}{q}"
        if routingType == "ferry" and radiuses:
            full_url += ("&" if q else "?") + f"radiuses={radiuses}"
        return full_url

    def build_gh_url(base_url):
        coords_part = path.split("/")[-1]
        points = []
        for coord in coords_part.split(";"):
            lon, lat = coord.split(",")
            points.append(f"point={lat}%2C{lon}")
        point_params = "&".join(points)

        details = "".join(f"&details={d}" for d in GH_DETAILS)
        full_url = f"{base_url}/route?{point_params}&type=json&profile={gh_profile}{details}"
        if waypoint_modes:
            full_url += f"&waypoint_modes={waypoint_modes}"

        if routingType == "ferry" and radiuses:
            full_url += f"&radiuses={radiuses}"
        return full_url

    def build_gh_body():
        # POST, as a query string can't carry custom_model. multiply_by stays 0:
        # values above 1 give wrong routes.
        body = {
            "profile": gh_profile,
            "points": [[float(c) for c in coord.split(",")] for coord in path.split("/")[-1].split(";")],
            "details": GH_DETAILS,
            "ch.disable": True,
            "custom_model": {"priority": [{"if": c, "multiply_by": "0"} for c in filters]},
        }
        if max_speed:
            # limit_to only ever lowers speeds
            body["custom_model"]["speed"] = [{"if": "true", "limit_to": str(max_speed)}]
        if waypoint_modes:
            body["waypoint_modes"] = waypoint_modes
        return body

    # (connect, read) timeouts — connect failures fail fast; reads cap at 8s
    TIMEOUT = (3, 8)

    # Behavior per type
    # OSRM routes come with their steps, whose junction classes tell the tunnels
    def osrm_url(base_url):
        url = build_url(base_url)
        return url if "steps=" in url else url + ("&" if "?" in url else "?") + "steps=true"

    if routingType == "bus" and not public_fallback:
        try:
            response = requests.get(osrm_url(base), timeout=TIMEOUT)
            return make_response(with_road_environment(response.json(), args), response.status_code)
        except (requests.RequestException, ValueError) as e:
            return make_response({"error": "routing upstream unavailable", "detail": str(e)}, 502)

    if routingType == "bus":
        routers_fallback_base = "https://routing.openstreetmap.de/routed-car"
        try:
            response = requests.get(osrm_url(base), timeout=(3, 5))
            if response.status_code != 200:
                raise Exception("Non-200 response")

            data = response.json()
            if data.get("status") == "NoRoute":
                raise Exception("Router responded with NoRoute")

            return make_response(with_road_environment(data, args), return_code)
        except Exception:
            fallback_url = osrm_url(routers_fallback_base)
            try:
                return make_response(with_road_environment(requests.get(fallback_url, timeout=TIMEOUT).json(), args), 235)
            except requests.RequestException as e:
                return make_response({"error": "routing upstream unavailable", "detail": str(e)}, 502)

    if routingType == "train" and use_new_router:
        try:
            if filters or max_speed:
                # A filter forcing a long detour takes seconds on the worldwide graph
                gh_json = requests.post(f"{base}/route", json=build_gh_body(), timeout=(3, 25)).json()
            else:
                gh_json = requests.get(build_gh_url(base), timeout=TIMEOUT).json()
        except requests.RequestException as e:
            return make_response({"error": "routing upstream unavailable", "detail": str(e)}, 502)
        if filters and not gh_json.get("paths"):
            return {"code": "NoRoute", "message": "No route matches these filters"}
        return convert_graphhopper_to_osrm(gh_json)

    # All other types (OSRM routers): their answer, with its tunnels
    try:
        response = requests.get(osrm_url(base), timeout=TIMEOUT)
    except requests.RequestException as e:
        return make_response({"error": "routing upstream unavailable", "detail": str(e)}, 502)
    try:
        return make_response(with_road_environment(response.json(), args), response.status_code)
    except ValueError:
        return response.text


# The routers the /status page checks (templates/status.html), and only those: the
# url comes from the browser, so anything else would let anyone make the server
# fetch any address, internal ones included.
STATUS_ROUTER_URLS = frozenset({
    "http://train.srv.trainlog.me",
    "http://train-gh.srv.trainlog.me",
    "http://bus.srv.trainlog.me",
    "https://busrouter.jkimball.dev",
    "https://routing.openstreetmap.de/routed-car",
    "http://ferry.srv.trainlog.me",
    "http://aerialway.srv.trainlog.me",
    "https://routing.openstreetmap.de/routed-foot",
    "https://routing.openstreetmap.de/routed-bike",
})


def router_health(url, profile):
    if url not in STATUS_ROUTER_URLS:
        return {"status": "ERROR", "message": "Unknown router"}
    dummy_profile = {"cycling": "bike", "bike": "bike", "foot": "foot", "walking": "foot", "walk": "foot"}.get(profile, "driving")
    try:
        if requests.get(f"{url}/health", timeout=3).status_code == 200:
            return {"status": "OK", "message": "healthy"}
    except requests.RequestException:
        pass
    try:
        dummy = requests.get(f"{url}/route/v1/{dummy_profile}/0,0.1;0,0.1", timeout=3)
        if dummy.status_code != 200:
            return {"status": "DOWN", "message": f"HTTP {dummy.status_code}"}
        if "routes" in dummy.json():
            return {"status": "OK", "message": "responding"}
        return {"status": "DOWN", "message": "no routes key"}
    except (requests.RequestException, ValueError) as e:
        return {"status": "DOWN", "message": type(e).__name__}
