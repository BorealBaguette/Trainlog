import logging

from src.paths import coords_to_ewkt
from src.pg import get_or_create_pg_session
from src.sql.plans import insert_plan_trip_query

from .plan_trip import PlanTrip

logger = logging.getLogger(__name__)


def create_plan_trip(plan_trip: PlanTrip, pg_session=None):
    """Insert a PlanTrip row with its path geometry inline (no separate paths row)."""
    coords = []
    for node in plan_trip.path or []:
        if isinstance(node, dict):
            coords.append([float(node["lat"]), float(node["lng"])])
        else:
            coords.append([float(node[0]), float(node[1])])
    ewkt = coords_to_ewkt(coords)  # None for empty path -> ST_GeomFromEWKT(NULL)

    with get_or_create_pg_session(pg_session) as pg:
        plan_trip.uid = pg.execute(
            insert_plan_trip_query(),
            {
                "plan_id": plan_trip.plan_id,
                "user_id": plan_trip.user_id,
                "sort_order": plan_trip.sort_order,
                "timing_mode": plan_trip.timing_mode,
                "start_day": plan_trip.start_day,
                "end_day": plan_trip.end_day,
                "start_time": plan_trip.start_time,
                "end_time": plan_trip.end_time,
                "start_datetime": plan_trip.start_datetime,
                "end_datetime": plan_trip.end_datetime,
                "utc_start_datetime": plan_trip.utc_start_datetime,
                "utc_end_datetime": plan_trip.utc_end_datetime,
                "estimated_trip_duration": plan_trip.estimated_trip_duration,
                "manual_trip_duration": plan_trip.manual_trip_duration,
                "origin_station": plan_trip.origin_station,
                "destination_station": plan_trip.destination_station,
                "trip_type": plan_trip.trip_type,
                "operator": plan_trip.operator,
                "line_name": plan_trip.line_name,
                "material_type": plan_trip.material_type,
                "material_type_advanced": plan_trip.material_type_advanced,
                "reg": plan_trip.reg,
                "seat": plan_trip.seat,
                "notes": plan_trip.notes,
                "trip_length": plan_trip.trip_length,
                "countries": plan_trip.countries,
                "price": plan_trip.price,
                "currency": plan_trip.currency,
                "purchase_date": plan_trip.purchase_date,
                "waypoints": plan_trip.waypoints,
                "visibility": plan_trip.visibility,
                "carbon": plan_trip.carbon,
                "power_type": plan_trip.power_type,
                "co2_override": plan_trip.co2_override,
                "geom_ewkt": ewkt,
                "created": plan_trip.created,
                "last_modified": plan_trip.last_modified,
            },
        ).fetchone()[0]

    logger.info(f"Created plan_trip {plan_trip.uid} in plan {plan_trip.plan_id}")
    return plan_trip.uid
