import logging

from flask import Blueprint, abort, jsonify, render_template, request, session

from src.commutes import (
    append_legs,
    create_commute,
    get_commute_template,
    log_commute_occurrence,
)
from src.pg import pg_session
from src.sql.commutes import (
    delete_commute_query,
    get_commute_query,
    get_commutes_for_append_query,
    get_commutes_query,
)
from src.utils import get_user_id, has_current_trip, lang, login_required

logger = logging.getLogger(__name__)

commutes_blueprint = Blueprint("commutes", __name__)


@commutes_blueprint.route("/u/<username>/commutes")
@login_required
def commute_list(username):
    user_id = get_user_id(username)
    with pg_session() as pg:
        rows = pg.execute(get_commutes_query(), {"user_id": user_id}).fetchall()
    commute_routes = [dict(row._mapping) for row in rows]

    userinfo = session.get("userinfo", {})
    user_lang = lang.get(userinfo.get("lang", "en"), {})
    return render_template(
        "commute_list.html",
        title=user_lang.get("manage_commutes"),
        username=username,
        commute_routes=commute_routes,
        **user_lang,
        **userinfo,
        nav="bootstrap/navigation.html",
        isCurrent=has_current_trip(),
    )


@commutes_blueprint.route("/u/<username>/commutes/list")
@login_required
def list_commutes_for_append(username):
    user_id = get_user_id(username)
    with pg_session() as pg:
        rows = pg.execute(
            get_commutes_for_append_query(), {"user_id": user_id}
        ).fetchall()
    return jsonify(
        [
            {
                "uid": row["uid"],
                "name": row["name"],
                "originStation": row["origin_station"],
                "destinationStation": row["destination_station"],
            }
            for row in rows
        ]
    )


@commutes_blueprint.route("/u/<username>/commutes/create", methods=["POST"])
@login_required
def create_commute_route(username):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "A commute name is required"}), 400

    user_id = get_user_id(username)

    try:
        trip_ids = _parse_trip_ids(data.get("tripIds"))
        commute_route_id = create_commute(user_id, name, trip_ids)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"commuteRouteId": commute_route_id})


@commutes_blueprint.route(
    "/u/<username>/commutes/<int:commute_uid>/append", methods=["POST"]
)
@login_required
def append_commute_legs(username, commute_uid):
    data = request.get_json(silent=True) or {}
    user_id = get_user_id(username)

    try:
        trip_ids = _parse_trip_ids(data.get("tripIds"))
        appended = append_legs(user_id, commute_uid, trip_ids)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except PermissionError:
        # Matches the other commute routes' _get_owned_commute_or_404: don't
        # distinguish "doesn't exist" from "exists but isn't yours".
        abort(404)

    return jsonify({"appended": appended})


@commutes_blueprint.route("/u/<username>/commutes/<int:commute_uid>/template")
@login_required
def get_commute_template_route(username, commute_uid):
    user_id = get_user_id(username)
    _get_owned_commute_or_404(commute_uid, user_id)

    legs = get_commute_template(commute_uid)
    return jsonify(
        [
            {
                "sequence": leg["sequence"],
                "originStation": leg["origin_station"],
                "destinationStation": leg["destination_station"],
                "scheduledStart": leg["scheduled_start"].strftime("%H:%M"),
                "scheduledEnd": leg["scheduled_end"].strftime("%H:%M"),
            }
            for leg in legs
        ]
    )


@commutes_blueprint.route(
    "/u/<username>/commutes/<int:commute_uid>/log", methods=["POST"]
)
@login_required
def log_occurrence_route(username, commute_uid):
    user_id = get_user_id(username)
    _get_owned_commute_or_404(commute_uid, user_id)

    data = request.get_json(silent=True) or {}
    date = data.get("date")
    legs = data.get("legs")
    if not date:
        return jsonify({"error": "A date is required"}), 400
    if not isinstance(legs, list) or not legs:
        return jsonify({"error": "No leg data provided"}), 400

    try:
        new_trip_ids = log_commute_occurrence(commute_uid, date, legs)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"newTripIds": new_trip_ids})


@commutes_blueprint.route(
    "/u/<username>/commutes/<int:commute_uid>/delete", methods=["POST"]
)
@login_required
def delete_commute_route(username, commute_uid):
    user_id = get_user_id(username)
    _get_owned_commute_or_404(commute_uid, user_id)

    with pg_session() as pg:
        pg.execute(delete_commute_query(), {"uid": commute_uid, "user_id": user_id})

    return jsonify({"success": True})


def _parse_trip_ids(raw) -> list[int]:
    if not raw:
        raise ValueError("No trips given")
    try:
        return [int(t) for t in raw]
    except (TypeError, ValueError):
        raise ValueError("Invalid trip ids")


def _get_owned_commute_or_404(commute_uid, user_id):
    with pg_session() as pg:
        row = pg.execute(
            get_commute_query(), {"uid": commute_uid, "user_id": user_id}
        ).fetchone()
    if row is None:
        abort(404)
    return row
