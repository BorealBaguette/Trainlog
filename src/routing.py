# src/routing.py
import requests
from urllib.parse import parse_qs
from flask import make_response

# Import these from wherever they currently live in your project
# Adjust imports to match your structure.
from src.router_regions import all_in_region
from src.graphhopper import convert_graphhopper_to_osrm     # example


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
GAUGES = ("1435", "1520", "1668", "1000", "1067")


def filter_conditions(query):
    """The conditions of the tracks to exclude, one per active filter, and the train's
    top speed (0 when uncapped)."""
    get = lambda key: query.get(key, [""])[0]
    conditions = []
    # A capped train is a classic one: off high-speed lines too, or a capped LGV would
    # still be its shortest way.
    max_speed = int(get("max_speed")) if get("max_speed").isdigit() else 0
    if get("avoid_highspeed") == "1" or max_speed:
        conditions.append("highspeed")
    power = [POWER_SYSTEMS[p] for p in get("power").split(",") if p in POWER_SYSTEMS]
    if get("electrified") == "yes" or power:
        conditions.append("electrified == NO")
    if power:
        conditions.append("!(" + " || ".join(f"({p})" for p in power) + " || voltage == 0)")
    if get("gauge") in GAUGES:
        conditions.append(f"gauge != 0 && gauge != {get('gauge')}")
    return conditions, max_speed


def forward_routing_core(routingType, path, flask_request, extra_args=None):
    # GraphHopper profile matching the original (pre-normalization) trip type
    gh_profile = {"tram": "tram", "metro": "metro"}.get(routingType, "train" if routingType in ("train", "rail", "funicular") else "all")

    # Normalize routing type
    if routingType in ("train", "tram", "metro", "funicular", "rail"):
        routingType = "train"
    elif routingType == "scooter":
        routingType = "cycle"

    radiuses = None
    use_new_router = False  # defined for all paths

    # Determine base URL + (optional) return_code for bus
    return_code = None

    if routingType == "train":
        use_new_router = flask_request.args.get("use_new_router", "false").lower() == "true"
        base = "https://train-gh.srv.trainlog.me" if use_new_router else "https://train.srv.trainlog.me"

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

        full_url = (
            f"{base_url}/route?"
            f"{point_params}&type=json&profile={gh_profile}&details=electrified&details=distance"
        )
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
            "details": ["electrified", "distance"],
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
    if routingType == "bus":
        routers_fallback_base = "https://routing.openstreetmap.de/routed-car"
        try:
            response = requests.get(build_url(base), timeout=(3, 5))
            if response.status_code != 200:
                raise Exception("Non-200 response")

            data = response.json()
            if data.get("status") == "NoRoute":
                raise Exception("Router responded with NoRoute")

            return make_response(data, return_code)
        except Exception:
            fallback_url = build_url(routers_fallback_base)
            try:
                return make_response(requests.get(fallback_url, timeout=TIMEOUT).json(), 235)
            except requests.RequestException as e:
                return make_response({"error": "routing upstream unavailable", "detail": str(e)}, 502)

    if routingType == "train" and use_new_router:
        try:
            if filters:
                # A filter forcing a long detour takes seconds on the worldwide graph
                gh_json = requests.post(f"{base}/route", json=build_gh_body(), timeout=(3, 25)).json()
            else:
                gh_json = requests.get(build_gh_url(base), timeout=TIMEOUT).json()
        except requests.RequestException as e:
            return make_response({"error": "routing upstream unavailable", "detail": str(e)}, 502)
        if filters and not gh_json.get("paths"):
            return {"code": "NoRoute", "message": "No route matches these filters"}
        return convert_graphhopper_to_osrm(gh_json)

    # All other types: just proxy text
    try:
        return requests.get(build_url(base), timeout=TIMEOUT).text
    except requests.RequestException as e:
        return make_response({"error": "routing upstream unavailable", "detail": str(e)}, 502)
