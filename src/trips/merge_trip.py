"""The stops of trips merged into one (mergeTrips): each trip's own stops, in order,
with the station where one trip ends and the next begins as a stop between them."""

import json
from datetime import datetime, timedelta

from py.utils import getCountryFromCoordinates
from src.utils import _timezone_finder


def _point(point):
    if isinstance(point, dict):
        return float(point["lat"]), float(point["lng"])
    return float(point[0]), float(point[1])


def _utc(value, delay=None):
    """A trip's 'YYYY-MM-DD HH:MM:SS' UTC time (plus a delay in seconds) as the stop
    records keep it, or None."""
    if not isinstance(value, str):
        return None
    dt = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S") + timedelta(seconds=delay or 0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _junction(arriving, leaving, point):
    """The waypoint where `arriving` ends and `leaving` begins, as a timetable stop."""
    lat, lng = point
    name = arriving["destination_station"] or ""
    # "🇷🇴 Brașov": the stop keeps the name and the country apart.
    if name and 0x1F1E6 <= ord(name[0]) <= 0x1F1FF:
        bare = name[2:].strip()
    else:
        bare = name
    stop = {
        "name": bare,
        "cc": getCountryFromCoordinates(lat, lng)["countryCode"],
        "arr": _utc(arriving["utc_end_datetime"]),
        "dep": _utc(leaving["utc_start_datetime"]),
        "platform": leaving["departure_platform"] or arriving["arrival_platform"],
        "tz": _timezone_finder.timezone_at(lat=lat, lng=lng),
        "lat": lat,
        "lng": lng,
    }
    if arriving["arrival_delay"]:
        stop["arr_rt"] = _utc(arriving["utc_end_datetime"], arriving["arrival_delay"])
    if leaving["departure_delay"]:
        stop["dep_rt"] = _utc(leaving["utc_start_datetime"], leaving["departure_delay"])
    stop = {k: v for k, v in stop.items() if v not in (None, "")}
    return {"lat": lat, "lng": lng, "name": name, "stop": stop, "hard": True}


def merged_stop_fields(trip_items):
    """The merged trip's waypoints, end platforms and delays, from processPublicTrips'
    sorted [{"trip", "path"}, ...]."""
    waypoints = []
    for i, item in enumerate(trip_items):
        previous = trip_items[i - 1] if i else None
        if previous and previous["path"]:
            waypoints.append(_junction(previous["trip"], item["trip"], _point(previous["path"][-1])))
        waypoints += json.loads(item["trip"]["waypoints"] or "[]")
    first, last = trip_items[0]["trip"], trip_items[-1]["trip"]
    return {
        "waypoints": json.dumps(waypoints),
        "departurePlatform": first["departure_platform"],
        "arrivalPlatform": last["arrival_platform"],
        "departure_delay": first["departure_delay"],
        "arrival_delay": last["arrival_delay"],
    }
