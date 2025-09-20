from flask import abort, Blueprint, jsonify, request

from src.trips import duplicate_trips
from src.utils import (
    getUser,
    get_trip_owner,
    get_user_id,
    login_required,
    mainConn,
    managed_cursor,
    load_config,
)
from src.models.user import User

trips_blueprint = Blueprint("trips", __name__)
owner = load_config()["owner"]


@trips_blueprint.route("/<username>/trips/bulkCopy", methods=["POST"])
@login_required
# Username is needed otherwise login_required fails
def bulk_copy_trips(username):
    data = request.get_json()
    trip_ids = data.get("tripIds")
    if trip_ids is None:
        abort(400)

    trip_ids = trip_ids.split(",")

    current_user_username = getUser()
    current_user_id = get_user_id(current_user_username)

    if not _trips_visible_to_user(trip_ids, current_user_username):
        abort(401)

    new_trip_ids = duplicate_trips(
        trip_ids=trip_ids,
        owner_id=current_user_id,
        owner_username=current_user_username,
    )

    # If theres been an error delete trips
    if len(new_trip_ids) != len(trip_ids):
        abort(500)

    return jsonify({"newTrips": new_trip_ids})


# When migradtion to PG is complete, update this to use user_id
def _trips_visible_to_user(trip_ids: list[int], current_user) -> bool:
    # Using this to avoid many public lookups if one user owns many trips
    trip_owner_visibilty_cache = {}

    with managed_cursor(mainConn) as cursor:
        for trip_id in trip_ids:
            cursor.execute(
                "SELECT username FROM trip WHERE uid = :trip_id", {"trip_id": trip_id}
            )
            row = cursor.fetchone()

            trip_owner = row["username"]

            is_public = trip_owner_visibilty_cache.get(trip_owner)

            if is_public is None:
                user = User.query.filter_by(username=trip_owner).first()
                trip_owner_visibilty_cache[trip_owner] = user.is_public_trips()

            is_public = trip_owner_visibilty_cache.get(trip_owner)

            if row is None:
                return False
            elif current_user not in (trip_owner, owner) and not is_public:
                return False
    return True
