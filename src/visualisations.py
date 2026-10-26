"""
Deck.gl / MapLibre visualisation pages for a single user's trip data.

Registered as a Flask Blueprint. Call register(app, fetch_trips_paths_fn)
from app.py after fetchTripsPaths is defined.
"""

import json
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, session, url_for

from src.pg import pg_session
from src.utils import get_user_id, lang, login_required

bp = Blueprint("viz", __name__)

# Injected by register() — avoids a circular import from app.py
_fetch_trips_paths = None


def register(app, fetch_trips_paths):
    global _fetch_trips_paths
    _fetch_trips_paths = fetch_trips_paths
    app.register_blueprint(bp)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_trip_dt(value):
    """Parse "YYYY-MM-DD HH:MM:SS" to Unix timestamp; None for sentinels."""
    if not value or value in (1, -1, "1", "-1"):
        return None
    try:
        return int(datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").timestamp())
    except (ValueError, TypeError):
        return None


def _t(key):
    """A string in the user's language."""
    return lang[session["userinfo"]["lang"]][key]


def _ctx(username):
    """Template context shared by all viz pages."""
    return dict(
        username=username,
        data_url=url_for("viz.trips_api", username=username),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


# Past-only WHERE clause used by multiple endpoints
_PAST_FILTER = """
    AND (
        (COALESCE(t.utc_start_datetime, t.start_datetime) IS NULL
         AND NOT COALESCE(t.is_project, FALSE))
        OR COALESCE(t.utc_start_datetime, t.start_datetime) <= NOW()
    )
"""


# ---------------------------------------------------------------------------
# Data API endpoints
# ---------------------------------------------------------------------------

@bp.route("/u/<username>/api/viz/trips.json")
@login_required
def trips_api(username):
    """Paths + interpolated timestamps for the hexagon / trip-replay pages."""
    data = _fetch_trips_paths(username, "2000-01-01T00:00:00.000000", public=0)
    trips = []
    for t in data["trips"]:
        path = t.get("path", [])
        if len(path) < 2:
            continue
        trip = t["trip"]
        coords = [[p[1], p[0]] for p in path]  # [lat,lng] → [lng,lat]
        n = len(coords)
        start_ts = _parse_trip_dt(trip.get("start_datetime"))
        end_ts   = _parse_trip_dt(trip.get("end_datetime"))
        timestamps = None
        if start_ts and end_ts and end_ts > start_ts:
            dur = end_ts - start_ts
            timestamps = [int(start_ts + dur * i / (n - 1)) for i in range(n)]
        entry = {
            "path": coords,
            "type": trip.get("type", "other"),
            "from": trip.get("origin_station") or "",
            "to":   trip.get("destination_station") or "",
        }
        if timestamps:
            entry["timestamps"] = timestamps
        trips.append(entry)
    return jsonify(trips)


@bp.route("/u/<username>/api/heatmap.geojson")
@login_required
def heatmap_geojson(username):
    """All past trips as simplified LineString GeoJSON for the heatmap."""
    user_id = get_user_id(username)
    features = []
    with pg_session() as pg:
        rows = pg.execute(
            f"""
            SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(p.geom, 0.00001)) AS geojson,
                   t.trip_type
            FROM   paths p
            JOIN   trips t ON t.trip_id = p.trip_id
            WHERE  t.user_id = :user_id
              AND  p.geom IS NOT NULL
              {_PAST_FILTER}
            LIMIT  20000
            """,
            {"user_id": user_id},
        ).fetchall()
    for row in rows:
        if not row["geojson"]:
            continue
        data = json.loads(row["geojson"])
        if data.get("type") == "LineString" and len(data.get("coordinates", [])) >= 2:
            features.append({
                "type": "Feature",
                "geometry": data,
                "properties": {"type": row["trip_type"] or "other"},
            })
    return jsonify({"type": "FeatureCollection", "features": features})


@bp.route("/u/<username>/api/calendar.json")
@login_required
def calendar_api(username):
    """Per-day trip count and km for the calendar heatmap.

    ?by=travel (default) — grouped by travel date (utc_start_datetime / start_datetime)
    ?by=log             — grouped by the date the trip was created (created)
    """
    user_id = get_user_id(username)
    by = request.args.get("by", "travel")

    if by == "log":
        sql = """
            SELECT DATE(created) AS day,
                   COUNT(*)                      AS trip_count,
                   COALESCE(SUM(trip_length), 0) / 1000.0 AS total_km
            FROM   trips
            WHERE  user_id = :user_id
              AND  created IS NOT NULL
              AND  NOT COALESCE(is_project, FALSE)
            GROUP  BY day
            ORDER  BY day
            """
    else:
        sql = """
            SELECT DATE(COALESCE(utc_start_datetime, start_datetime)) AS day,
                   COUNT(*)                      AS trip_count,
                   COALESCE(SUM(trip_length), 0) / 1000.0 AS total_km
            FROM   trips
            WHERE  user_id = :user_id
              AND  COALESCE(utc_start_datetime, start_datetime) IS NOT NULL
              AND  COALESCE(utc_start_datetime, start_datetime) <= NOW()
              AND  NOT COALESCE(is_project, FALSE)
            GROUP  BY day
            ORDER  BY day
            """
    with pg_session() as pg:
        rows = pg.execute(sql, {"user_id": user_id}).fetchall()
    return jsonify([
        {"day": str(r["day"]), "trips": r["trip_count"],
         "km": round(float(r["total_km"] or 0), 1)}
        for r in rows
    ])


@bp.route("/u/<username>/api/arc_trips.json")
@login_required
def arc_trips_api(username):
    """Start/end point pairs for the arc flow map."""
    user_id = get_user_id(username)
    with pg_session() as pg:
        rows = pg.execute(
            f"""
            SELECT ST_X(ST_StartPoint(p.geom)) AS src_lng,
                   ST_Y(ST_StartPoint(p.geom)) AS src_lat,
                   ST_X(ST_EndPoint(p.geom))   AS dst_lng,
                   ST_Y(ST_EndPoint(p.geom))   AS dst_lat,
                   t.trip_type
            FROM   paths p
            JOIN   trips t ON t.trip_id = p.trip_id
            WHERE  t.user_id = :user_id
              AND  p.geom IS NOT NULL
              AND  ST_NPoints(p.geom) >= 2
              {_PAST_FILTER}
            """,
            {"user_id": user_id},
        ).fetchall()
    return jsonify([
        {"src": [r["src_lng"], r["src_lat"]],
         "dst": [r["dst_lng"], r["dst_lat"]],
         "type": r["trip_type"] or "other"}
        for r in rows
        if r["src_lng"] is not None and r["dst_lng"] is not None
    ])


@bp.route("/u/<username>/api/airport_graph.json")
@login_required
def airport_graph_api(username):
    """Flights between airports for the airport graph, by direction and year.

    Air trips name their airports "… (CDG)"; the trailing code is the node,
    looked up in `airports` for its city, country and position.
    """
    user_id = get_user_id(username)
    with pg_session() as pg:
        rows = pg.execute(
            f"""
            SELECT substring(t.origin_station FROM :code_re)      AS src,
                   substring(t.destination_station FROM :code_re) AS dst,
                   min(t.origin_station)      AS src_name,
                   min(t.destination_station) AS dst_name,
                   EXTRACT(YEAR FROM COALESCE(t.utc_start_datetime, t.start_datetime))::int AS year,
                   count(*) AS flights
            FROM   trips t
            WHERE  t.user_id = :user_id
              AND  t.trip_type = 'air'
              {_PAST_FILTER}
            GROUP  BY 1, 2, 5
            """,
            {"user_id": user_id, "code_re": r"\(([^()]+)\)\s*$"},
        ).fetchall()
        rows = [r for r in rows if r["src"] and r["dst"]]

        # Name as the trip wrote it, without its flag, for codes `airports` doesn't know.
        names = {}
        for r in rows:
            names.setdefault(r["src"], r["src_name"])
            names.setdefault(r["dst"], r["dst_name"])
        airports = pg.execute(
            """
            SELECT DISTINCT ON (iata) iata, name, city, iso_country, latitude, longitude
            FROM   airports
            WHERE  iata = ANY(:codes)
            ORDER  BY iata
            """,
            {"codes": list(names)},
        ).fetchall() if names else []

    known = {a["iata"]: a for a in airports}
    nodes = []
    for code, trip_name in names.items():
        a = known.get(code)
        nodes.append({
            "code": code,
            "name": a["name"] if a else trip_name.split(" ", 1)[-1],
            "city": (a["city"] if a else None) or "",
            "country": (a["iso_country"] if a else None) or "",
            "lat": a["latitude"] if a else None,
            "lng": a["longitude"] if a else None,
        })
    return jsonify({
        "nodes": nodes,
        "edges": [
            {"src": r["src"], "dst": r["dst"], "year": r["year"], "count": r["flights"]}
            for r in rows
        ],
    })


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@bp.route("/u/<username>/viz")
@login_required
def index(username):
    return render_template("viz/index.html", title=_t("vizTitle"), **_ctx(username))


@bp.route("/u/<username>/viz/bestagons")
@login_required
def bestagons(username):
    return render_template("viz/bestagons.html", title=_t("vizHexagons"), **_ctx(username))


@bp.route("/u/<username>/viz/heatmap")
@login_required
def heatmap(username):
    return render_template("viz/heatmap.html", title=_t("vizHeatmap"), **_ctx(username))


@bp.route("/u/<username>/viz/calendar")
@login_required
def calendar(username):
    from flask import url_for as _uf
    return render_template(
        "viz/calendar.html", title=_t("vizCalendar"),
        trips_url=_uf("dynamic_trips", username=username, time="trips"),
        **_ctx(username),
    )


@bp.route("/u/<username>/viz/arcs")
@login_required
def arcs(username):
    return render_template("viz/arcs.html", title=_t("vizArcs"), **_ctx(username))


@bp.route("/u/<username>/viz/trips")
@login_required
def trips_page(username):
    return render_template("viz/trips.html", title=_t("vizReplay"), **_ctx(username))


@bp.route("/u/<username>/viz/airports")
@login_required
def airports_page(username):
    return render_template("viz/airports.html", title=_t("vizAirports"), **_ctx(username))
