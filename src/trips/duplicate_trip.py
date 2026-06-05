import logging

from src.pg import pg_session
from src.sql.trips import duplicate_trip_new_user_query, duplicate_trip_query

logger = logging.getLogger(__name__)


def duplicate_trips(trip_ids: list[int], owner_id: int) -> list[int]:
    new_trip_ids = []
    for trip_id in trip_ids:
        new_trip_ids.append(_duplicate_trip(trip_id, owner_id))
    return new_trip_ids


def _duplicate_trip(trip_id: int, owner_id: int) -> int:
    with pg_session() as pg:
        new_trip_id = pg.execute(
            duplicate_trip_new_user_query(),
            {"trip_id": trip_id, "new_user_id": owner_id},
        ).fetchone()[0]
        pg.execute(
            "INSERT INTO paths (trip_id, geom, altitude, timestamps)"
            " SELECT :new_id, geom, altitude, timestamps FROM paths WHERE trip_id = :old_id"
            " ON CONFLICT (trip_id) DO UPDATE SET geom = EXCLUDED.geom,"
            " altitude = EXCLUDED.altitude, timestamps = EXCLUDED.timestamps",
            {"new_id": new_trip_id, "old_id": trip_id},
        )

    logger.info(f"Successfully duplicated trip {trip_id} into {new_trip_id}")
    return new_trip_id


def duplicate_trip(trip_id: int):
    with pg_session() as pg:
        # PostgreSQL generates the new trip_id (SERIAL) and returns it.
        new_trip_id = pg.execute(
            duplicate_trip_query(), {"trip_id": trip_id}
        ).fetchone()[0]
        # Copy the route geometry (and any 3D flight track) to the new trip.
        pg.execute(
            "INSERT INTO paths (trip_id, geom, altitude, timestamps)"
            " SELECT :new_id, geom, altitude, timestamps FROM paths WHERE trip_id = :old_id"
            " ON CONFLICT (trip_id) DO UPDATE SET geom = EXCLUDED.geom,"
            " altitude = EXCLUDED.altitude, timestamps = EXCLUDED.timestamps",
            {"new_id": new_trip_id, "old_id": trip_id},
        )

    logger.info(f"Successfully duplicated trip {trip_id} into {new_trip_id}")
    return new_trip_id
