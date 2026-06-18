"""Create a plan_trip from a structured ("parsed") payload — the plan-world analog
of src.ai.create_trip_from_parsed. Reuses the same geocoding/routing enrichment
(enrich_parsed_trip) so an external AI (via the MCP server) can add a leg to a plan
by supplying the same fields it uses for real trips, plus plan timing.
"""

import json
import logging
from datetime import datetime

from py.utils import getCountriesFromPath, getCountryFromCoordinates
from src.ai import enrich_parsed_trip
from src.plans.create_plan_trip import create_plan_trip
from src.plans.plan_trip import PlanTrip
from src.plans.process_plan_dates import process_plan_dates
from src.utils import get_default_trip_visibility

logger = logging.getLogger(__name__)


def create_plan_trip_from_parsed(
    plan, parsed, timing_input, *, booked=False, visibility=None, pg_session=None
):
    """Geocode/route `parsed`, materialise `timing_input` against the plan's anchor
    date, and write a plan_trip. Returns the PlanTrip (with .uid) or None if the
    origin/destination could not be resolved.

    - plan: mapping with uid, user_id, anchor_date.
    - parsed: same shape as src.ai (type, origin/destination, *_lat/_lng, *_iata, …).
    - timing_input: a builder-style dict for process_plan_dates (precision + day/date
      /time keys).
    """
    parsed = enrich_parsed_trip(parsed)
    if not parsed:
        return None

    trip_type = parsed.get("type", "train")
    path = parsed["_path"]  # list of {lat, lng}
    trip_length = parsed["_distance"]

    if trip_type in ("air", "helicopter"):
        countries = {
            getCountryFromCoordinates(path[0]["lat"], path[0]["lng"])["countryCode"]: trip_length / 2,
            getCountryFromCoordinates(path[-1]["lat"], path[-1]["lng"])["countryCode"]: trip_length / 2,
        }
        countries = json.dumps(countries)
    else:
        countries = getCountriesFromPath(path, trip_type, None, None)

    timing = process_plan_dates(timing_input, path, plan["anchor_date"])

    # Routed legs with concrete UTC instants get a precise estimated duration.
    estimated = None
    if timing.get("utc_start_datetime") and timing.get("utc_end_datetime"):
        estimated = int(
            (timing["utc_end_datetime"] - timing["utc_start_datetime"]).total_seconds()
        )
        if estimated < 0:
            estimated = None

    now = datetime.now()
    plan_trip = PlanTrip(
        plan_id=plan["uid"],
        user_id=plan["user_id"],
        origin_station=parsed["_resolved_origin"],
        destination_station=parsed["_resolved_destination"],
        trip_type=trip_type,
        operator=parsed.get("operator"),
        line_name=parsed.get("line_name"),
        material_type=parsed.get("aircraft_icao") if trip_type == "air" else None,
        material_type_advanced=None,
        reg=None,
        seat=parsed.get("seat"),
        notes=parsed.get("notes"),
        trip_length=trip_length,
        estimated_trip_duration=estimated,
        countries=countries,
        price=parsed.get("price"),
        currency=parsed.get("currency"),
        waypoints=None,
        visibility=visibility or get_default_trip_visibility(trip_type),
        path=path,
        timing=timing,
        booked=booked,
        created=now,
        last_modified=now,
    )
    create_plan_trip(plan_trip, pg_session)
    logger.info(f"Created plan_trip {plan_trip.uid} in plan {plan['uid']} (mcp)")
    return plan_trip
