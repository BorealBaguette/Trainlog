"""The 3D trip view and the trips' video (alpha, linked only for one's own trips): the trips on
the terrain, with the ground's heights under them and the tunnel/bridge stretches their router
gave when they were saved (paths.altitude and paths.track_env, see src/track_profile.py). Like
the poster, only the viewer's own trips."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, jsonify, render_template, request, session, url_for

from py.utils import getCountryFromCoordinates
from src.paths import geom_geojson_to_coords
from src.pg import pg_session
from src.track_profile import TRACK_TYPES, cumulative_distances, fill_gaps, smooth_profile
from src.trip_selections import parse_trip_ids
from src.users import User
from src.utils import get_user_id, getUser, has_current_trip, lang, owner

trip3d_blueprint = Blueprint("trip3d", __name__)


def _local(value):
    """A stored local datetime as "YYYY-MM-DDTHH:MM", or None."""
    return value.strftime("%Y-%m-%dT%H:%M") if isinstance(value, datetime) else None


def timetable_stops(waypoints_json):
    """The trip's intermediate timetable stops, in order: name, place and local arrival and
    departure ("HH:MM"), for the video's station markers and clock. Routing points without
    a stop are left out."""
    try:
        waypoints = json.loads(waypoints_json) if isinstance(waypoints_json, str) else (waypoints_json or [])
    except ValueError:
        return []
    out = []
    for wp in waypoints if isinstance(waypoints, list) else []:
        stop = wp.get("stop") if isinstance(wp, dict) else None
        if not isinstance(stop, dict) or stop.get("lat") is None:
            continue
        tz = ZoneInfo(stop["tz"]) if stop.get("tz") else ZoneInfo("UTC")

        def local(value):
            try:
                return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(tz).strftime("%H:%M")
            except (TypeError, ValueError):
                return None

        out.append({"name": stop.get("name"), "lat": stop["lat"], "lng": stop["lng"],
                    "arr": local(stop.get("arr")), "dep": local(stop.get("dep"))})
    return out


TRIP_QUERY = """
    SELECT t.trip_id, t.user_id, t.origin_station, t.destination_station, t.trip_type,
           t.start_datetime, t.end_datetime, t.utc_start_datetime, t.utc_end_datetime, t.waypoints, t.visibility,
           ST_AsGeoJSON(p.geom) AS geojson, p.altitude, p.track_env
    FROM trips t JOIN paths p ON p.trip_id = t.trip_id
    WHERE t.trip_id = ANY(:ids)
"""


def country_runs(coords):
    """Where the path changes country: [[point index, "gb"], ...], each country from the
    point it starts at, for the video's flag. Read every 2 km, and between two readings that
    differ narrowed down to the point by halving; at sea (no country) the last one holds."""
    def code(i):
        cc = (getCountryFromCoordinates(lat=coords[i][0], lng=coords[i][1]) or {}).get("countryCode")
        return cc.lower() if cc and cc != "UN" else None  # UN: at sea

    if not coords:
        return []
    dist = cumulative_distances(coords)
    samples, next_at = [], 0.0
    for i, d in enumerate(dist):
        if d >= next_at or i == len(coords) - 1:
            samples.append(i)
            next_at = d + 2000
    runs, last, seen = [], None, 0
    for i in samples:
        c = code(i)
        if c is None:
            continue
        if c == last:
            seen = i
            continue
        start = i
        if runs:
            # The first point of the new country after the last reading of the previous one
            lo, hi = seen, i
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if code(mid) == c:
                    hi = mid
                else:
                    lo = mid
            start = hi
        runs.append([start, c])
        last, seen = c, i
    return runs


def _epoch_ms(value):
    """A stored UTC datetime as epoch milliseconds, or None."""
    return int(value.replace(tzinfo=timezone.utc).timestamp() * 1000) if isinstance(value, datetime) else None


def _payload(row, user=None):
    """A trip's path ([lng, lat]), its raw and cleaned heights, its tunnel/bridge ranges,
    its times and its timetable stops, and who took it (for a tag of several travellers)."""
    coords = geom_geojson_to_coords(row["geojson"])  # [[lat, lng], ...]
    air = row["trip_type"] == "air"
    raw = row["altitude"] if row["trip_type"] in TRACK_TYPES or air else None
    env = row["track_env"]
    return {
        "trip": {
            "id": row["trip_id"],
            "origin": row["origin_station"],
            "destination": row["destination_station"],
            "type": row["trip_type"],
            "start": _local(row["start_datetime"]),
            "end": _local(row["end_datetime"]),
            "utc": [_epoch_ms(row["utc_start_datetime"]), _epoch_ms(row["utc_end_datetime"])],
            "user": user,
        },
        "stops": timetable_stops(row["waypoints"]),
        "coords": [[lng, lat] for lat, lng in coords],
        "raw": raw,
        # A flight's recorded altitude is its height as flown, not to be smoothed
        "height": (fill_gaps(raw) if air else smooth_profile(coords, raw, env)) if raw else None,
        "env": env,
        "countries": country_runs(coords),
    }


def trips_video_json(trip_ids):
    """Several trips for the video, in the order they were taken. Like the poster, the
    viewer's own trips (the site owner may see any); with them, for a trip taken together,
    the other travellers' trips they have not kept private."""
    try:
        ids = parse_trip_ids(trip_ids)
    except ValueError:
        abort(404)
    with pg_session() as pg:
        rows = pg.execute(TRIP_QUERY, {"ids": ids}).fetchall()
    if not rows:
        abort(404)
    viewer = get_user_id()
    others = [row for row in rows if row["user_id"] != viewer]
    if not session.get(owner) and others and (
        len(others) == len(rows) or any(row["visibility"] == "private" for row in others)
    ):
        abort(401)
    rows = sorted(rows, key=lambda r: (r["utc_start_datetime"] or r["start_datetime"] or datetime.min, r["trip_id"]))
    owners = {uid: User.query.filter_by(uid=uid).first() for uid in {row["user_id"] for row in rows}}
    trips = []
    for row in rows:
        trip = _payload(row, owners[row["user_id"]].username if owners[row["user_id"]] else None)
        # The owner's premium membership, shown on their video's Trainlog chip
        trip["trip"]["premium"] = bool(owners[row["user_id"]] and owners[row["user_id"]].premium)
        trips.append(trip)
    return jsonify({"trips": trips})


def _page(template, tripIds, **extra):
    username = getUser()
    user = User.query.filter_by(username=username).first() if username != "public" else None
    return render_template(
        template,
        title="3D trip view",
        nav="bootstrap/no_user_nav.html" if username == "public" else "bootstrap/navigation.html",
        username=username,
        tileserver=user.tileserver if user else "default",
        isCurrent=has_current_trip(),
        tripIds=tripIds,
        urls={
            "maplibre": url_for("trip3d.view", tripIds=tripIds),
            "giro": url_for("trip3d.giro", tripIds=tripIds),
            "video": url_for("public_trip_video", tripIds=tripIds),
        },
        **extra,
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


@trip3d_blueprint.route("/public/trip/<tripIds>/3d")
def view(tripIds):
    return _page("admin/trip3d.html", tripIds)


# The same view drawn with Giro3D, to compare with the MapLibre one
@trip3d_blueprint.route("/public/trip/<tripIds>/3d/giro")
def giro(tripIds):
    return _page("admin/trip3d_giro.html", tripIds)


# The ids in the body: a tag's run to hundreds, too many for a URL
@trip3d_blueprint.route("/public/trips/3d.json", methods=["POST"])
def trips_json():
    return trips_video_json(str((request.get_json(silent=True) or {}).get("ids", "")))


# A trip several people took together (a shared tag, or the trips of a multiTrip link):
# everyone at once, on the real clock
@trip3d_blueprint.route("/public/multiTrip/<tripIds>/video")
@trip3d_blueprint.route("/public/multiTrip/tag/<tagId>/video")
def group_video(tripIds=None, tagId=None):
    tag_name = None
    if tagId:
        with pg_session() as pg:
            row = pg.execute(
                """
                SELECT string_agg(a.trip_id::text, ',') AS trip_ids, tags.name
                FROM tags JOIN tags_associations a ON a.tag_id = tags.uid
                WHERE tags.uuid = :uuid GROUP BY tags.name
                """,
                {"uuid": tagId},
            ).fetchone()
        if row is None:
            abort(404)
        tripIds, tag_name = row["trip_ids"], row["name"]
    return _page("admin/trip3d_group_video.html", tripIds, tag_uuid=tagId, tag_description=tag_name)
