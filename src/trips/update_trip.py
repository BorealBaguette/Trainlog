import logging

from flask import abort

from src.operators import sync_trip_operators
from src.paths import coords_to_ewkt
from src.pg import pg_session
from src.sql.trips import update_trip_query
from src.utils import get_username, owner

from .trip import Trip

logger = logging.getLogger(__name__)


def update_trip(trip_id: int, trip: Trip, formData=None, updateCreated=False):
    with pg_session() as pg:
        # Ownership check against PostgreSQL.
        row = pg.execute(
            "SELECT user_id FROM trips WHERE trip_id = :trip_id", {"trip_id": trip_id}
        ).fetchone()
        if row is None:
            abort(404)  # Trip does not exist
        if get_username() not in (get_username(row["user_id"]), owner):
            abort(404)  # Trip does not belong to the user

        pg.execute(
            update_trip_query(),
            {
                "trip_id": trip_id,
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
                "price": trip.price if trip.price != "" else None,
                "currency": trip.currency,
                "ticket_id": trip.ticket_id if trip.ticket_id != "" else None,
                "purchase_date": trip.purchasing_date,
                "carbon": trip.carbon,
                "visibility": trip.visibility if trip.visibility != "" else None,
                "departure_delay": trip.departure_delay,
                "arrival_delay": trip.arrival_delay,
                "power_type": trip.power_type,
                "co2_override": trip.co2_override,
                "route_source": trip.route_source,
            },
        )

        # The operator text and the trip type both feed the resolution, and either
        # may have changed here.
        sync_trip_operators(trip_id, pg_session_=pg)

        # Update the route geometry. trip.path may be [[lat,lng],...] or
        # [{"lat":..,"lng":..},...] depending on caller; normalise to [lat,lng].
        coords = trip.path or []
        if coords and isinstance(coords[0], dict):
            coords = [[c["lat"], c["lng"]] for c in coords]
        ewkt = coords_to_ewkt(coords)
        if ewkt is not None:
            # The 3D flight track (altitude/timestamps) must stay aligned with the
            # geometry. If the route itself was (re-)imported/edited (formData carries
            # a "path"), replace the track with whatever came with it — fresh FR24
            # arrays, or NULL for a GPX/manual path (clears the now-stale track).
            # A metadata-only edit reuses the existing geometry, so leave it intact.
            if formData is not None and "path" in formData:
                pg.execute(
                    "INSERT INTO paths (trip_id, geom, altitude, timestamps)"
                    " VALUES (:trip_id, ST_GeomFromEWKT(:ewkt),"
                    " CAST(:altitude AS jsonb), CAST(:timestamps AS jsonb))"
                    " ON CONFLICT (trip_id) DO UPDATE SET geom = EXCLUDED.geom,"
                    " altitude = EXCLUDED.altitude, timestamps = EXCLUDED.timestamps",
                    {
                        "trip_id": trip_id,
                        "ewkt": ewkt,
                        "altitude": getattr(trip, "altitude", None),
                        "timestamps": getattr(trip, "timestamps", None),
                    },
                )
            else:
                pg.execute(
                    "INSERT INTO paths (trip_id, geom) VALUES (:trip_id, ST_GeomFromEWKT(:ewkt))"
                    " ON CONFLICT (trip_id) DO UPDATE SET geom = EXCLUDED.geom",
                    {"trip_id": trip_id, "ewkt": ewkt},
                )

    logger.info(f"Successfully updated trip {trip_id}")
