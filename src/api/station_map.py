"""The stations map: every station of the user's trips, coloured by trip type (src/station_map.py)."""

from flask import Blueprint, jsonify, render_template, session

from src.station_map import user_stations
from src.users import User
from src.utils import get_user_id, has_current_trip, lang, login_required

station_map_blueprint = Blueprint("station_map", __name__)


@station_map_blueprint.route("/u/<username>/stations/map")
@login_required
def stations_map(username):
    user = User.query.filter_by(username=username).first()
    return render_template(
        "station_map.html",
        title=lang[session["userinfo"]["lang"]]["stations"],
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id(username)),
        tileserver=user.tileserver,
        globe=user.globe,
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@station_map_blueprint.route("/u/<username>/stations/map/data")
@login_required
def stations_map_data(username):
    return jsonify(user_stations(get_user_id(username)))
