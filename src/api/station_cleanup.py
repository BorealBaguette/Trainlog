"""Tidy stations: the user's trips grouped by the station their ends are really at, to give
every name a station has gathered over time the one it has now (src/station_cleanup.py)."""

import logging

from flask import Blueprint, jsonify, render_template, request, session

from src.station_cleanup import rename_station, station_groups
from src.utils import get_user_id, has_current_trip, lang, login_required

logger = logging.getLogger(__name__)

station_cleanup_blueprint = Blueprint("station_cleanup", __name__)


@station_cleanup_blueprint.route("/u/<username>/stations/tidy")
@login_required
def tidy_stations(username):
    return render_template(
        "station_cleanup.html",
        title=lang[session["userinfo"]["lang"]]["tidyStations"],
        username=username,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(get_user_id(username)),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@station_cleanup_blueprint.route("/u/<username>/stations/tidy/data")
@login_required
def tidy_stations_data(username):
    try:
        groups, unmatched = station_groups(get_user_id(username))
    except Exception as e:
        logger.warning(f"Tidy stations for {username} failed: {e}")
        return jsonify(error="unavailable"), 502
    return jsonify(groups=groups, unmatched=unmatched)


@station_cleanup_blueprint.route("/u/<username>/stations/tidy/rename", methods=["POST"])
@login_required
def tidy_stations_rename(username):
    body = request.get_json(silent=True) or {}
    to = (body.get("to") or "").strip()
    station_key = (body.get("station_key") or "").strip()[:100] or None
    labels = [label for label in body.get("labels") or [] if isinstance(label, str)]
    try:
        origin_ids = [int(i) for i in body.get("origin_ids") or []]
        destination_ids = [int(i) for i in body.get("destination_ids") or []]
    except (TypeError, ValueError):
        return jsonify(error="trip ids must be numbers"), 400
    if not to or len(to) > 300 or not labels:
        return jsonify(error="a name and the names it replaces are required"), 400
    renamed = rename_station(get_user_id(username), to, station_key, labels, origin_ids,
                             destination_ids)
    return jsonify(renamed=renamed)
