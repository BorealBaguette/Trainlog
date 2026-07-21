"""GPX ingest + GPSLogger import helpers.

Pure data-prep extracted from app.py: parse uploaded GPX files into staging
rows, geocode endpoints, and (for the automatic GPSLogger import) build a
``newTrip`` payload — optionally snapped to the network with smart routing — that
app.py hands to ``saveTripToDb``. ``saveTripToDb`` itself stays in app.py because
of its deep coupling there; everything that can live outside app.py lives here.
"""

import json
import logging
import re
from io import BytesIO

import gpxpy
from werkzeug.datastructures import FileStorage

from py.gps_cleaner import clean_gps_route
from py.utils import get_flag_emoji, getDistance
from src.pg import pg_session
from src.photon import photonRequest
from src.routing import forward_routing_core
from src.utils import getLocalDatetime

logger = logging.getLogger(__name__)

# Trip types whose GPS tracks can be snapped to a routable network.
ROUTABLE_TRIP_TYPES = {
    "train", "metro", "tram", "funicular", "rail", "ferry", "aerialway",
    "bus", "car", "walk", "cycle", "scooter",
}


class GpxIngestError(ValueError):
    """Raised when an uploaded file can't be processed."""


def getAddressFromCoords(lat, lng):
    """Reverse-geocode a coordinate to a "flag City - Suburb" label via Photon."""
    response_json = photonRequest("/reverse", {"lon": lng, "lat": lat, "lang": "en"})

    if response_json is None or not response_json.get("features"):
        return ""

    props = response_json["features"][0]["properties"]
    country_code = props.get("countrycode", "").upper()
    city = props.get("city") or props.get("county") or ""
    suburb = props.get("suburb") or props.get("district") or ""

    flag = get_flag_emoji(country_code)
    return f"{flag} {city}" + (f" - {suburb}" if suburb else "")


def cluster_waypoints(waypoints, min_distance_meters=10):
    """
    Group waypoints that are within min_distance_meters of each other
    and return the average position for each cluster.

    :param waypoints: List of {"lat": float, "lng": float} waypoints
    :param min_distance_meters: Minimum distance in meters to consider points as separate
    :return: List of simplified waypoints
    """
    from math import asin, cos, radians, sin, sqrt

    def haversine(lat1, lon1, lat2, lon2):
        """Calculate the great circle distance between two points in meters"""
        # Convert decimal degrees to radians
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])

        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))
        # Radius of earth in meters
        r = 6371000
        return c * r

    if not waypoints:
        return []

    simplified = []
    current_cluster = [waypoints[0]]

    for i in range(1, len(waypoints)):
        # Check distance from current point to the first point in the current cluster
        distance = haversine(
            waypoints[i]["lat"],
            waypoints[i]["lng"],
            current_cluster[0]["lat"],
            current_cluster[0]["lng"],
        )

        if distance <= min_distance_meters:
            # Add to current cluster
            current_cluster.append(waypoints[i])
        else:
            # Calculate the average position for the cluster
            avg_lat = sum(p["lat"] for p in current_cluster) / len(current_cluster)
            avg_lng = sum(p["lng"] for p in current_cluster) / len(current_cluster)
            simplified.append({"lat": avg_lat, "lng": avg_lng})

            # Start a new cluster with the current point
            current_cluster = [waypoints[i]]

    # Don't forget to add the last cluster
    if current_cluster:
        avg_lat = sum(p["lat"] for p in current_cluster) / len(current_cluster)
        avg_lng = sum(p["lng"] for p in current_cluster) / len(current_cluster)
        simplified.append({"lat": avg_lat, "lng": avg_lng})

    return simplified


# Tasker placeholders that weren't substituted (e.g. <ele>%GPS_ALT%</ele>) would
# break gpxpy parsing — strip these tags before parsing.
_TASKER_PLACEHOLDER_RE = re.compile(r"<(course|speed|ele|hdop|vdop|pdop)>%[^<]+</\1>")


def clean_tasker_gpx_files(files):
    """Return copies of `files` with unreplaced Tasker placeholders stripped."""
    cleaned = []
    for f in files:
        f.stream.seek(0)
        text = f.stream.read().decode("utf-8", errors="replace")
        text = _TASKER_PLACEHOLDER_RE.sub("", text)
        cleaned.append(
            FileStorage(
                stream=BytesIO(text.encode("utf-8")),
                filename=f.filename,
                content_type=f.content_type,
            )
        )
    return cleaned


def parse_gpx_files(files, source, username, notes=""):
    """Parse GPX files into staging-row dicts (origin/destination/times/path/...).

    `files` is a list of werkzeug FileStorage-like objects exposing `.filename`
    and `.stream`. Raises GpxIngestError(message) for invalid input.
    """
    gpx_rows = []
    for file in files:
        if not file.filename.endswith(".gpx"):
            raise GpxIngestError(f"{file.filename} is not a valid GPX file")

        try:
            gpx = gpxpy.parse(file.stream)
        except gpxpy.gpx.GPXException as e:
            raise GpxIngestError(f"{file.filename} is not readable GPX: {e}") from e

        points = None
        start_time = None
        end_time = None
        distance = 0

        # Handle Tracks
        if gpx.tracks and any(track.segments for track in gpx.tracks):
            all_points = []
            total_distance = 0
            first_time = None
            last_time = None

            # 1. Gather all points from all segments
            for track in gpx.tracks:
                for segment in track.segments:
                    if segment.points:
                        if first_time is None:
                            first_time = segment.points[0].time  # Only set start once
                        last_time = segment.points[-1].time  # Continuously update end
                        all_points.extend(segment.points)

            # 2. Compute total distance across *all* points (including "gaps" between segments)
            for i in range(1, len(all_points)):
                total_distance += getDistance(
                    {
                        "lat": all_points[i - 1].latitude,
                        "lng": all_points[i - 1].longitude,
                    },
                    {"lat": all_points[i].latitude, "lng": all_points[i].longitude},
                )

            points = all_points
            start_time = first_time
            end_time = last_time
            distance = total_distance

        # Handle Routes
        elif gpx.routes and gpx.routes[0].points:
            points = gpx.routes[0].points
            # Routes typically don't include timestamps; set start/end times to None
            start_time = None
            end_time = None
            # Approximate route distance by summing distances between consecutive points
            for i in range(len(points) - 1):
                distance += gpxpy.geo.distance(
                    points[i].latitude,
                    points[i].longitude,
                    0,
                    points[i + 1].latitude,
                    points[i + 1].longitude,
                    0,
                )

        if not points:
            raise GpxIngestError(f"No points found in {file.filename}")

        # Generate path in [[lat, lng], [lat, lng]] format
        path = json.dumps([[point.latitude, point.longitude] for point in points])

        start_point = points[0]
        end_point = points[-1]

        origin = getAddressFromCoords(lat=start_point.latitude, lng=start_point.longitude)
        destination = getAddressFromCoords(lat=end_point.latitude, lng=end_point.longitude)

        # Calculate duration (only for tracks with timestamps)
        duration = 0
        if start_time and end_time:
            duration = int((end_time - start_time).total_seconds())

            # Convert to local time and format to "YYYY-MM-DD HH:MM"
            start_time = getLocalDatetime(
                start_point.latitude, start_point.longitude, start_time
            ).strftime("%Y-%m-%d %H:%M")
            end_time = getLocalDatetime(
                end_point.latitude, end_point.longitude, end_time
            ).strftime("%Y-%m-%d %H:%M")

        gpx_rows.append(
            {
                "source": source,
                "username": username,
                "origin": origin,
                "destination": destination,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "distance": int(distance),
                "path": path,
                "notes": notes,
            }
        )

    return gpx_rows


def ingest_gpx_files(username, source, files, notes=""):
    """Parse GPX files and insert one staging row per file into the `gpx` table.

    Shared by the browser upload form (handle_gpx_upload) and the
    token-authenticated GPSLogger endpoint. Returns the inserted rows.
    """
    gpx_rows = parse_gpx_files(files, source, username, notes)

    with pg_session() as pg:
        for gpx_row in gpx_rows:
            pg.execute(
                """
                INSERT INTO gpx (source, username, origin, destination, start_time, end_time, duration, distance, path, notes)
                VALUES (:source, :username, :origin, :destination, :start_time, :end_time, :duration, :distance, :path, :notes)
                """,
                gpx_row,
            )

    return gpx_rows


def parse_trip_params(args):
    """Map upload-URL query params to ``newTrip`` overrides.

    Lets the GPSLogger/Tasker URL fill a trip without later supervision, e.g.
    ``?operator=SNCF&material_type=TGV&electric=1&line_name=Sud-Est``.
    """

    def _get(*keys):
        for k in keys:
            v = args.get(k)
            if v not in (None, ""):
                return v
        return None

    params = {
        "operator": _get("operator") or "",
        "lineName": _get("line_name", "lineName") or "",
    }

    for key, *aliases in [
        ("material_type",),
        ("material_type_advanced",),
        ("seat",),
        ("reg",),
        ("visibility",),
        ("ticket_id",),
        ("currency",),
        ("notes",),
    ]:
        v = _get(key, *aliases)
        if v is not None:
            params[key] = v

    price = _get("price")
    if price is not None:
        try:
            params["price"] = float(price)
        except ValueError:
            pass

    # Power type, e.g. auto / electric / thermic / manual.
    power = _get("power_type", "powerType")
    if power:
        params["powerType"] = power

    co2 = _get("co2_override", "co2Override")
    if co2 is not None:
        try:
            params["co2Override"] = float(co2)
        except ValueError:
            pass

    return params


def build_trip_payload(row, trip_type, params, use_routing, flask_request):
    """Build a ``(newTrip, path)`` pair ready for saveTripToDb from a parsed/staged
    GPX row, applying URL param overrides and optional smart routing.

    `row` has the keys produced by parse_gpx_files (origin, destination,
    start_time, end_time, distance, duration, path, notes). `path` may be a JSON
    string (staging table) or already-decoded list of [lat, lng].
    """

    def _to_iso(value):
        if value in (None, ""):
            return -1
        if isinstance(value, str):
            return value.replace(" ", "T")
        return value.isoformat()

    start_time = _to_iso(row.get("start_time"))
    end_time = _to_iso(row.get("end_time"))
    duration = row.get("duration") or 0
    notes = params.get("notes") if params.get("notes") is not None else (row.get("notes") or "")
    precision = "preciseDates" if start_time != -1 else "unknown"

    raw_path = row["path"]
    coords = json.loads(raw_path) if isinstance(raw_path, str) else raw_path
    raw_waypoints = [{"lat": point[0], "lng": point[1]} for point in coords]

    newTrip = {
        "type": trip_type,
        "originStation": [None, row.get("origin") or ""],
        "destinationStation": [None, row.get("destination") or ""],
        "newTripStart": start_time,
        "newTripEnd": end_time,
        "trip_length": row.get("distance"),
        "estimated_trip_duration": duration,
        "operator": params.get("operator", ""),
        "lineName": params.get("lineName", ""),
        "price": params.get("price"),
        "currency": params.get("currency"),
        "purchasing_date": None,
        "precision": precision,
        "notes": notes,
        "onlyDateDuration": "",
        "unknownType": "past",
        "waypoints": json.dumps([]),
    }

    for key in (
        "material_type",
        "material_type_advanced",
        "seat",
        "reg",
        "visibility",
        "ticket_id",
    ):
        if params.get(key) is not None:
            newTrip[key] = params[key]
    if params.get("powerType"):
        newTrip["powerType"] = params["powerType"]
    if params.get("co2Override") is not None:
        newTrip["co2Override"] = params["co2Override"]

    if use_routing and trip_type in ROUTABLE_TRIP_TYPES:
        cleaning_result = clean_gps_route(
            raw_waypoints=raw_waypoints,
            forwardRouting=lambda path, routingType, options=None: forward_routing_core(
                routingType=routingType,
                path=path,
                flask_request=flask_request,
                extra_args=options,
            ),
            trip_type=trip_type,
            deviation_threshold=800,
            max_search_points=75,
        )
        if cleaning_result["success"]:
            path = cleaning_result["path"]
            newTrip["trip_length"] = cleaning_result["distance"]
            newTrip["estimated_trip_duration"] = cleaning_result["duration"]
            newTrip["waypoints"] = json.dumps(cleaning_result["waypoints"])
        else:
            logger.warning(
                "Smart routing failed (%s); using basic clustering.",
                cleaning_result.get("error"),
            )
            path = raw_waypoints
            newTrip["waypoints"] = json.dumps(cluster_waypoints(raw_waypoints, 20))
    else:
        path = raw_waypoints
        newTrip["waypoints"] = json.dumps(cluster_waypoints(raw_waypoints, 20))

    return newTrip, path
