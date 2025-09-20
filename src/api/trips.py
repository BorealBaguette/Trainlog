from flask import abort, Blueprint, jsonify, request

from src.trips import duplicate_trips
from src.utils import get_user_id, getUser, get_trip_owner

trips_blueprint = Blueprint('trips', __name__)


@trips_blueprint.route("/trips/bulkCopy", methods=["POST"])
def bulk_copy_trips():
    data = request.get_json()
    trip_ids = data.get("tripIds")
    if trip_ids is None:
        abort(400)

    trip_ids = trip_ids.split(",")

    # Check all trips are public, or you are the owner etc

    current_user_username = getUser()
    current_user_id = get_user_id(current_user_username)

    for trip_id in trip_ids:
        pass
        

    new_trip_ids = duplicate_trips(
        trip_ids=trip_ids,
        owner_id=current_user_id,
        owner_username=current_user_username
    )

    # If theres been an error delete trips
    if len(new_trip_ids) != len(trip_ids):
        abort(500)

    return jsonify({'newTrips': new_trip_ids})
