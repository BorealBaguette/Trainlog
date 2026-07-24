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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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


class _TrackPoint:
    """Uniform track point (GPX and KML share this). `time` is a tz-aware UTC
    datetime or None; `elevation` is metres or None."""

    __slots__ = ("latitude", "longitude", "elevation", "time")

    def __init__(self, latitude, longitude, elevation=None, time=None):
        self.latitude = latitude
        self.longitude = longitude
        self.elevation = elevation
        self.time = time


def _path_distance(points):
    """Sum of great-circle distances (metres) between consecutive points."""
    total = 0.0
    for i in range(1, len(points)):
        total += getDistance(
            {"lat": points[i - 1].latitude, "lng": points[i - 1].longitude},
            {"lat": points[i].latitude, "lng": points[i].longitude},
        )
    return total


def _finalize_row(points, source, username, notes):
    """Build a staging-row dict from an ordered point list.

    Captures per-point elevation/time as `altitude`/`timestamps` JSON arrays
    (parallel to `path`, None when the source carries none), so a later unrouted
    import can persist a full 3D track. Shared by the GPX and KML parsers.
    """
    path = json.dumps([[p.latitude, p.longitude] for p in points])

    altitude = [p.elevation for p in points]
    timestamps = [int(p.time.timestamp()) if p.time else None for p in points]
    altitude = json.dumps(altitude) if any(a is not None for a in altitude) else None
    timestamps = (
        json.dumps(timestamps) if any(t is not None for t in timestamps) else None
    )

    start_point, end_point = points[0], points[-1]
    origin = getAddressFromCoords(lat=start_point.latitude, lng=start_point.longitude)
    destination = getAddressFromCoords(lat=end_point.latitude, lng=end_point.longitude)

    start_time = start_point.time
    end_time = end_point.time
    duration = 0
    if start_time and end_time:
        duration = int((end_time - start_time).total_seconds())
        # Store the local wall-clock time at each endpoint, "YYYY-MM-DD HH:MM".
        start_time = getLocalDatetime(
            start_point.latitude, start_point.longitude, start_time
        ).strftime("%Y-%m-%d %H:%M")
        end_time = getLocalDatetime(
            end_point.latitude, end_point.longitude, end_time
        ).strftime("%Y-%m-%d %H:%M")
    else:
        start_time = end_time = None

    return {
        "source": source,
        "username": username,
        "origin": origin,
        "destination": destination,
        "start_time": start_time,
        "end_time": end_time,
        "duration": duration,
        "distance": int(_path_distance(points)),
        "path": path,
        "altitude": altitude,
        "timestamps": timestamps,
        "notes": notes,
    }


def parse_gpx_files(files, source, username, notes=""):
    """Parse GPX files into staging-row dicts (origin/destination/times/path/...).

    `files` is a list of werkzeug FileStorage-like objects exposing `.filename`
    and `.stream`. Raises GpxIngestError(message) for invalid input.
    """
    gpx_rows = []
    for file in files:
        if not file.filename.lower().endswith(".gpx"):
            raise GpxIngestError(f"{file.filename} is not a valid GPX file")

        try:
            gpx = gpxpy.parse(file.stream)
        except gpxpy.gpx.GPXException as e:
            raise GpxIngestError(f"{file.filename} is not readable GPX: {e}") from e

        points = []
        # Tracks (usually timed): concatenate every segment of every track.
        if gpx.tracks and any(track.segments for track in gpx.tracks):
            for track in gpx.tracks:
                for segment in track.segments:
                    points.extend(segment.points)
        # Routes (usually untimed) as a fallback.
        elif gpx.routes and gpx.routes[0].points:
            points = list(gpx.routes[0].points)

        if not points:
            raise GpxIngestError(f"No points found in {file.filename}")

        gpx_rows.append(_finalize_row(points, source, username, notes))

    return gpx_rows


# KML local tag names we care about; matched namespace-agnostically (Google's gx:
# extension and the default KML namespace both appear, sometimes mixed).
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _parse_kml_time(text):
    """Parse a KML <when> ISO-8601 timestamp into a tz-aware UTC datetime, or None."""
    if not text:
        return None
    text = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _kml_points(root):
    """Extract an ordered point list from a KML document.

    Prefers gx:Track (which carries <when> times paired with <gx:coord> triples);
    falls back to LineString <coordinates> (untimed). KML orders coordinates
    lng,lat[,ele] — the reverse of Trainlog's [lat, lng]."""
    points = []
    # gx:Track — timed. <when> and <coord> children are parallel by position.
    for el in root.iter():
        if _local(el.tag) != "Track":
            continue
        whens = [c.text for c in el if _local(c.tag) == "when"]
        coords = [c.text for c in el if _local(c.tag) == "coord"]
        for i, coord in enumerate(coords):
            parts = (coord or "").split()
            if len(parts) < 2:
                continue
            lng, lat = float(parts[0]), float(parts[1])
            ele = float(parts[2]) if len(parts) >= 3 else None
            t = _parse_kml_time(whens[i]) if i < len(whens) else None
            points.append(_TrackPoint(lat, lng, ele, t))
    if points:
        return points

    # LineString coordinates — untimed. Scan only inside a LineString so Point/
    # Polygon coordinates elsewhere aren't swept in.
    for el in root.iter():
        if _local(el.tag) != "LineString":
            continue
        for child in el:
            if _local(child.tag) != "coordinates" or not child.text:
                continue
            for tok in child.text.split():
                parts = tok.split(",")
                if len(parts) < 2:
                    continue
                lng, lat = float(parts[0]), float(parts[1])
                ele = float(parts[2]) if len(parts) >= 3 else None
                points.append(_TrackPoint(lat, lng, ele, None))
    return points


def parse_kml_files(files, source, username, notes=""):
    """Parse KML files into the same staging-row dicts as parse_gpx_files."""
    rows = []
    for file in files:
        if not file.filename.lower().endswith(".kml"):
            raise GpxIngestError(f"{file.filename} is not a valid KML file")
        file.stream.seek(0)
        try:
            root = ET.fromstring(file.stream.read())
        except ET.ParseError as e:
            raise GpxIngestError(f"{file.filename} is not readable KML: {e}") from e
        points = _kml_points(root)
        if not points:
            raise GpxIngestError(f"No track points found in {file.filename}")
        rows.append(_finalize_row(points, source, username, notes))
    return rows


def parse_track_files(files, source, username, notes=""):
    """Parse a mixed batch of .gpx/.kml uploads by extension into staging rows."""
    rows = []
    for file in files:
        name = (file.filename or "").lower()
        if name.endswith(".kml"):
            rows.extend(parse_kml_files([file], source, username, notes))
        else:
            rows.extend(parse_gpx_files([file], source, username, notes))
    return rows


def ingest_gpx_files(username, source, files, notes=""):
    """Parse GPX/KML files and insert one staging row per file into the `gpx` table.

    Shared by the browser upload form (handle_gpx_upload) and the
    token-authenticated GPSLogger endpoint. Returns the inserted rows. Accepts a
    mixed .gpx/.kml batch; elevation/time are staged when the source carries them.
    """
    gpx_rows = parse_track_files(files, source, username, notes)

    with pg_session() as pg:
        for gpx_row in gpx_rows:
            pg.execute(
                """
                INSERT INTO gpx (source, username, origin, destination, start_time, end_time, duration, distance, path, altitude, timestamps, notes)
                VALUES (:source, :username, :origin, :destination, :start_time, :end_time, :duration, :distance, :path, CAST(:altitude AS jsonb), CAST(:timestamps AS jsonb), :notes)
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

    # Per-point elevation/time captured at parse time, aligned to the raw track.
    # Normalized to JSON strings (staging returns jsonb as lists). They only stay
    # valid while the raw track is the stored path — routing rewrites the geometry,
    # so they are dropped in that branch below.
    def _as_json(v):
        if v is None or isinstance(v, str):
            return v
        return json.dumps(v)

    altitude = _as_json(row.get("altitude"))
    timestamps = _as_json(row.get("timestamps"))

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
            # Routing resampled the geometry — the raw-track arrays no longer align.
            altitude = timestamps = None
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

    return newTrip, path, altitude, timestamps
