"""The 3D trip view (alpha, admin only, not linked anywhere): a rail trip on the terrain,
with the track heights and tunnel/bridge stretches the rail router gave it when it was
saved (paths.altitude and paths.track_env, see src/track_profile.py). Only the viewer's
own trips, while it is a development tool."""

from flask import abort, jsonify, render_template, request, session

from src.paths import geom_geojson_to_coords
from src.pg import pg_session
from src.track_profile import smooth_profile
from src.users import User
from src.utils import get_user_id, getUser, has_current_trip, lang

RAIL_TYPES = ("train", "tram", "metro", "funicular", "rail")


def trip3d_page(trip_id=None):
    user = User.query.filter_by(username=getUser()).first()
    with pg_session() as pg:
        # The viewer's latest rail trips saved with track heights, to pick from.
        recent = pg.execute(
            """
            SELECT t.trip_id, t.origin_station, t.destination_station, t.trip_type
            FROM paths p JOIN trips t ON t.trip_id = p.trip_id
            WHERE t.user_id = :uid AND p.track_env IS NOT NULL AND p.altitude IS NOT NULL
            ORDER BY t.trip_id DESC
            LIMIT 30
            """,
            {"uid": get_user_id()},
        ).fetchall()
    # Two renderers side by side while they are compared: CesiumJS, or MapLibre with
    # three.js for the route (?engine=three)
    engine = "three" if request.args.get("engine") == "three" else "cesium"
    return render_template(
        "admin/trip3d_three.html" if engine == "three" else "admin/trip3d.html",
        engine=engine,
        title="3D trip view",
        nav="bootstrap/navigation.html",
        username=getUser(),
        tileserver=user.tileserver if user else "default",
        isCurrent=has_current_trip(),
        trip_id=trip_id,
        recent=[dict(r._mapping) for r in recent],
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


def trip3d_json(trip_id):
    """The trip's path ([lng, lat]), its raw and cleaned track heights and its
    tunnel/bridge ranges. 404 for someone else's trip or one without a path."""
    with pg_session() as pg:
        row = pg.execute(
            """
            SELECT t.trip_id, t.origin_station, t.destination_station, t.trip_type,
                   ST_AsGeoJSON(p.geom) AS geojson, p.altitude, p.track_env
            FROM trips t JOIN paths p ON p.trip_id = t.trip_id
            WHERE t.trip_id = :tid AND t.user_id = :uid
            """,
            {"tid": trip_id, "uid": get_user_id()},
        ).fetchone()
    if row is None:
        abort(404)
    coords = geom_geojson_to_coords(row["geojson"])  # [[lat, lng], ...]
    raw = row["altitude"] if row["trip_type"] in RAIL_TYPES else None
    env = row["track_env"]
    return jsonify({
        "trip": {
            "id": row["trip_id"],
            "origin": row["origin_station"],
            "destination": row["destination_station"],
            "type": row["trip_type"],
        },
        "coords": [[lng, lat] for lat, lng in coords],
        "raw": raw,
        "height": smooth_profile(coords, raw, env, row["trip_type"]) if raw else None,
        "env": env,
    })
