"""The station explorer, read only: quai's stations as Trainlog takes them, for every user.
Admins edit them from the same page under /admin/station_explorer (src/api/admin)."""

from functools import wraps

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from src.quai import QUAI_MODES, apply_overrides, quai_get, quai_station, station_label, station_merges
from src.users import User
from src.utils import get_user_id, getUser, has_current_trip, lang

station_explorer_blueprint = Blueprint("station_explorer", __name__)


def logged_in(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)

    return decorated_function


def explorer_page(editable):
    username = getUser()
    user = User.query.filter_by(username=username).first()
    return render_template(
        "admin/station_explorer.html",
        nav="bootstrap/navigation.html",
        username=username,
        editable=editable,
        tileserver=user.tileserver if user else "default",
        isCurrent=has_current_trip(get_user_id()),
        modes=sorted(set(QUAI_MODES.values())),
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


def quai_json(path, params=None):
    data = quai_get(path, params)
    if data is None:
        return jsonify(error="quai unavailable"), 502
    return jsonify(data)


@station_explorer_blueprint.route("/stations/explorer")
@logged_in
def station_explorer():
    return explorer_page(editable=False)


@station_explorer_blueprint.route("/stations/explorer/search")
@logged_in
def search():
    return quai_json("search", {
        "q": request.args.get("q", ""),
        "mode": request.args.get("mode") or None,
        "lang": request.args.get("lang") or None,
        "limit": 20,
    })


@station_explorer_blueprint.route("/stations/explorer/station/<mode>/<key>")
@logged_in
def station(mode, key):
    data = quai_get(f"station/{mode}/{key}", {
        "objects": 1,
        "lang": request.args.get("lang") or None,
    })
    if data is None:
        return jsonify(error="quai unavailable"), 502
    if not data:
        return jsonify(error="unknown station"), 404
    # quai's position and names, before an override changes them, then Trainlog's name for
    # the station.
    # Shown as it is, not as the station it may be merged into, with what it is merged into
    # and what is merged into it.
    station = data["station"]
    station["osm_lat"], station["osm_lng"] = station["lat"], station["lng"]
    station["osm_tracks"] = station.get("tracks") or []
    station["osm_names"] = dict(station.get("names") or {})
    apply_overrides([station], follow_merges=False)
    station["trainlog_name"] = station_label(station)

    def named(key):
        other = quai_station(mode, key)
        return {"station_key": key, "name": station_label(other) if other else key}

    merges = station_merges()
    target = merges.get((mode, station["station_key"]))
    station["merged_into"] = named(target) if target else None
    station["merged_from"] = [named(k) for (m, k), t in merges.items()
                              if m == mode and t == station["station_key"]]
    return jsonify(data)


@station_explorer_blueprint.route("/stations/explorer/station/<mode>/<key>/line")
@logged_in
def line(mode, key):
    return quai_json(f"station/{mode}/{key}/line", {"ref": request.args.get("ref", "")})


@station_explorer_blueprint.route("/stations/explorer/station/<mode>/<key>/services")
@logged_in
def services(mode, key):
    return quai_json(f"station/{mode}/{key}/services")


@station_explorer_blueprint.route("/stations/explorer/route/<int:relation_id>")
@logged_in
def route(relation_id):
    return quai_json(f"route/{relation_id}")
