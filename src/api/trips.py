from flask import Blueprint, abort, jsonify, request

from src.pg import pg_session
from src.trips.duplicate_trip import duplicate_trips
from src.users import User
from src.utils import (
    current_user_is_friend_with,
    get_user_id,
    getUser,
    login_required,
)

trips_blueprint = Blueprint("trips", __name__)


@trips_blueprint.route("/<username>/trips/bulkCopy", methods=["POST"])
@login_required
# Username is needed otherwise login_required fails
def bulk_copy_trips(username):
    data = request.get_json()
    trip_ids = str(data.get("tripIds"))
    if trip_ids is None:
        abort(400)

    if "," in trip_ids:
        trip_ids = [int(id) for id in trip_ids.split(",")]
    else:
        trip_ids = [int(trip_ids)]

    current_user_username = getUser()
    current_user_id = get_user_id(current_user_username)

    if not _trips_visible_to_user(trip_ids, current_user_id):
        abort(401)

    new_trip_ids = duplicate_trips(
        trip_ids=trip_ids,
        owner_id=current_user_id,
    )

    if len(new_trip_ids) != len(trip_ids):
        abort(500)

    return jsonify({"newTrips": new_trip_ids})


def _trips_visible_to_user(trip_ids: list[int], current_user_id: int) -> bool:
    user_cache = {}
    friend_cache = set()

    with pg_session() as pg:
        rows = pg.execute(
            "SELECT user_id, visibility"
            " FROM trips"
            f" WHERE trip_id IN ({','.join(str(i) for i in trip_ids)})"
        ).fetchall()

    if not rows:
        return False
    if len(rows) != len(trip_ids):
        return False

    for trip_user_id, visibility in rows:
        if current_user_id == trip_user_id:
            continue

        if visibility == 'private':
            return False

        trip_owner = user_cache.get(trip_user_id) or User.query.filter_by(uid=trip_user_id).first()
        user_cache[trip_user_id] = trip_owner

        if visibility == 'friends':
            if trip_user_id in friend_cache:
                continue
            if current_user_is_friend_with(trip_owner.username):
                friend_cache.add(trip_user_id)
                continue
            return False

        if not trip_owner.is_public_trips():
            return False

    return True
