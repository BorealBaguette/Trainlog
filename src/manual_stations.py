"""A user's manual stations: the places they named by hand, listed, added, edited and deleted."""

import re

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from py.utils import get_flag_emoji, getCountryFromCoordinates
from src.pg import pg_session
from src.trip_types import TRIP_TYPES
from src.utils import get_user_id, has_current_trip, lang, login_required

manual_stations_blueprint = Blueprint("manual_stations", __name__)


def _owned(pg, username, uid):
    row = pg.execute("SELECT creator FROM manual_stations WHERE uid = :uid", {"uid": uid}).fetchone()
    if row is None or row["creator"] != username:
        abort(401)


def _named(name, lat, lng):
    """The name as Trainlog saves a station's, "🇫🇷 Name": the flag of where it is, in place of
    any it had."""
    name = re.sub(r"^[\U0001F1E6-\U0001F1FF]{2}\s*", "", (name or "").strip())
    if not name or len(name) > 200:
        abort(400)
    return f"{get_flag_emoji(getCountryFromCoordinates(lat, lng)['countryCode'])} {name}"


def _point():
    """The form's position, checked, as (lat, lng)."""
    try:
        lat, lng = float(request.form["lat"]), float(request.form["lng"])
    except (KeyError, ValueError):
        abort(400)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        abort(400)
    return lat, lng


@manual_stations_blueprint.route("/u/<username>/manualStations")
@login_required
def userManualStations(username):
    with pg_session() as pg:
        stationsList = [
            dict(row._mapping)
            for row in pg.execute(
                "SELECT * FROM manual_stations WHERE creator = :creator"
                " ORDER BY station_type, name",
                {"creator": username},
            ).fetchall()
        ]
    texts = lang[session["userinfo"]["lang"]]
    return render_template(
        "manual_stations.html",
        stationsList=stationsList,
        # The trip types a station can be added for, in their usual order and words.
        stationTypes=[(t, texts.get(t, t)) for t in TRIP_TYPES if t != "air"],
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id()),
        **texts,
        **session["userinfo"],
    )


@manual_stations_blueprint.route("/u/<username>/manualStations/add", methods=["POST"])
@login_required
def addUserManualStation(username):
    station_type = request.form.get("station_type")
    if station_type not in TRIP_TYPES:
        abort(400)
    lat, lng = _point()
    name = _named(request.form.get("name"), lat, lng)
    with pg_session() as pg:
        pg.execute(
            "INSERT INTO manual_stations (creator, name, lat, lng, station_type)"
            " VALUES (:creator, :name, :lat, :lng, :station_type)",
            {"creator": username, "name": name, "lat": lat, "lng": lng, "station_type": station_type},
        )
    return redirect(url_for("manual_stations.userManualStations", username=username))


@manual_stations_blueprint.route("/u/<username>/manualStations/<int:id>/update", methods=["POST"])
@login_required
def updateUserManualStation(username, id):
    lat, lng = _point()
    name = _named(request.form.get("name"), lat, lng)
    with pg_session() as pg:
        _owned(pg, username, id)
        pg.execute(
            """
            UPDATE manual_stations
            SET name = :name, lat = :lat, lng = :lng
            WHERE uid = :uid
            """,
            {"name": name, "lat": lat, "lng": lng, "uid": id},
        )
    return redirect(url_for("manual_stations.userManualStations", username=username))


@manual_stations_blueprint.route("/u/<username>/manualStations/<int:id>/delete", methods=["POST"])
@login_required
def deleteUserManualStation(username, id):
    with pg_session() as pg:
        _owned(pg, username, id)
        pg.execute("DELETE FROM manual_stations WHERE uid = :uid", {"uid": id})
    return redirect(url_for("manual_stations.userManualStations", username=username))
