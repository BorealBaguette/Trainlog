import time

from flask import Blueprint, jsonify, request
from geopip._geopip import GeoPIP

from py.utils import getCountryFromCoordinates
from src.pg import pg_session
from src.users import User

live_map_blueprint = Blueprint("live_map", __name__)

# How long the navbar's count of travellers is reused before it is counted again, per worker
LIVE_COUNT_TTL = 120
_live_count = {"at": 0, "value": 0}

# GBIF's continent cookie cutter (ODbL): its polygons extend over the sea, so pins
# offshore still get a continent
_continents = GeoPIP(filename="static/data/continents.geojson")


def live_travellers_count():
    """How many people the live map shows right now, for the navbar."""
    if time.monotonic() - _live_count["at"] < LIVE_COUNT_TTL:
        return _live_count["value"]
    # The same trips and users as get_current_trips_data(public_only=True)
    with pg_session() as pg:
        user_ids = [
            row[0]
            for row in pg.execute(
                """
                SELECT DISTINCT user_id FROM trips
                WHERE (utc_start_datetime + COALESCE(departure_delay, 0) * interval '1 second') <= NOW()
                  AND (utc_end_datetime + COALESCE(arrival_delay, 0) * interval '1 second') >= NOW()
                  AND (visibility = 'public' OR (visibility IS NULL AND trip_type NOT IN ('poi', 'accommodation', 'restaurant', 'walk', 'cycle', 'car')))
                """
            ).fetchall()
        ]
    value = (
        User.query.filter(User.uid.in_(user_ids), User.appear_on_global).count()
        if user_ids
        else 0
    )
    _live_count.update(at=time.monotonic(), value=value)
    return value


@live_map_blueprint.route("/live_map/countries", methods=["POST"])
def live_map_countries():
    """[country, continent] under each [lat, lng] the live map sends (its pins), the country null at sea."""
    points = (request.get_json(silent=True) or [])[:500]
    places = []
    for lat, lng in points:
        lat, lng = float(lat), float(lng)
        code = getCountryFromCoordinates(lat, lng)["countryCode"]
        continent = _continents.search(lng=lng, lat=lat)
        places.append(
            [None if code == "UN" else code, continent and continent["continent"]]
        )
    return jsonify(places)
