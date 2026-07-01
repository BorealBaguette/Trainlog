import logging

from src.paths import Path, coords_to_ewkt
from src.pg import get_or_create_pg_session
from src.sql.trips import insert_trip_query

from .trip import Trip

logger = logging.getLogger(__name__)


def create_trip(trip: Trip, pg_session=None):
    with get_or_create_pg_session(pg_session) as pg:
        # PostgreSQL generates the trip_id (SERIAL) and returns it.
        trip.trip_id = pg.execute(
            insert_trip_query(),
            {
                "user_id": trip.user_id,
                "origin_station": trip.origin_station,
                "destination_station": trip.destination_station,
                "start_datetime": trip.start_datetime,
                "end_datetime": trip.end_datetime,
                "is_project": trip.is_project,
                "utc_start_datetime": trip.utc_start_datetime,
                "utc_end_datetime": trip.utc_end_datetime,
                "estimated_trip_duration": trip.estimated_trip_duration,
                "manual_trip_duration": trip.manual_trip_duration,
                "trip_length": trip.trip_length,
                "operator": trip.operator,
                "countries": trip.countries,
                "line_name": trip.line_name,
                "created": trip.created,
                "last_modified": trip.last_modified,
                "trip_type": trip.type,
                "material_type": trip.material_type,
                "material_type_advanced": trip.material_type_advanced,
                "seat": trip.seat,
                "reg": trip.reg,
                "waypoints": trip.waypoints,
                "notes": trip.notes,
                "price": trip.price,
                "currency": trip.currency,
                "ticket_id": trip.ticket_id,
                "purchase_date": trip.purchasing_date,
                "carbon": trip.carbon,
                "visibility": trip.visibility,
                "departure_delay": trip.departure_delay,
                "arrival_delay": trip.arrival_delay,
                "power_type": trip.power_type,
                "co2_override": trip.co2_override,
                "route_source": trip.route_source,
            },
        ).fetchone()[0]

        # Write the route geometry to PostGIS in the same session.
        path = (
            trip.path
            if isinstance(trip.path, Path)
            else Path(path=trip.path or [], trip_id=trip.trip_id)
        )
        path.set_trip_id(trip.trip_id)
        ewkt = coords_to_ewkt([[node.lat, node.lng] for node in path.list])
        if ewkt is not None:
            # altitude/timestamps are JSON-string arrays aligned with the geom
            # vertices (None for non-flights / paths without a 3D track).
            pg.execute(
                "INSERT INTO paths (trip_id, geom, altitude, timestamps)"
                " VALUES (:trip_id, ST_GeomFromEWKT(:ewkt),"
                " CAST(:altitude AS jsonb), CAST(:timestamps AS jsonb))"
                " ON CONFLICT (trip_id) DO UPDATE SET geom = EXCLUDED.geom,"
                " altitude = EXCLUDED.altitude, timestamps = EXCLUDED.timestamps",
                {
                    "trip_id": trip.trip_id,
                    "ewkt": ewkt,
                    "altitude": getattr(trip, "altitude", None),
                    "timestamps": getattr(trip, "timestamps", None),
                },
            )

    logger.info(f"Successfully created trip {trip.trip_id}")
    return trip.trip_id
