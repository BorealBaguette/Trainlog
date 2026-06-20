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

    if not _trips_visible_to_user(trip_ids, current_user_username):
        abort(401)

    new_trip_ids = duplicate_trips(
        trip_ids=trip_ids,
        owner_id=current_user_id,
    )

    if len(new_trip_ids) != len(trip_ids):
        abort(500)

    return jsonify({"newTrips": new_trip_ids})


def _trips_visible_to_user(trip_ids: list[int], current_user: str) -> bool:
    user_cache = {}
    friend_cache = set()

    with pg_session() as pg:
        rows = pg.execute(
            "SELECT t.trip_id, u.username, t.visibility"
            " FROM trips t JOIN \"user\" u ON t.user_id = u.uid"
            f" WHERE t.trip_id IN ({','.join(str(i) for i in trip_ids)})"
        ).fetchall()

    if not rows:
        return False
    if len(rows) != len(trip_ids):
        return False

    for trip in rows:
        trip_owner_username = trip[1]

        if current_user == trip_owner_username:
            continue

        if trip[2] == 'private':
            return False

        if trip[2] == 'friends':
            if trip_owner_username in friend_cache:
                continue
            elif current_user_is_friend_with(trip_owner_username):
                friend_cache.add(trip_owner_username)
                continue
            else:
                return False

        user_public = user_cache.get(trip_owner_username)
        if user_public is None:
            user_public = User.query.filter_by(username=trip_owner_username).first().is_public_trips()
            user_cache[trip_owner_username] = user_public

        if not user_public:
            return False

    return True
