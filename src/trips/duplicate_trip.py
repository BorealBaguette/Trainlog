import logging

from src.pg import pg_session
from src.sql.trips import duplicate_trip_query

logger = logging.getLogger(__name__)


def duplicate_trip(trip_id: int):
    with pg_session() as pg:
        # PostgreSQL generates the new trip_id (SERIAL) and returns it.
        new_trip_id = pg.execute(
            duplicate_trip_query(), {"trip_id": trip_id}
        ).fetchone()[0]
        # Copy the route geometry to the new trip.
        pg.execute(
            "INSERT INTO paths (trip_id, geom) SELECT :new_id, geom FROM paths WHERE trip_id = :old_id"
            " ON CONFLICT (trip_id) DO UPDATE SET geom = EXCLUDED.geom",
            {"new_id": new_trip_id, "old_id": trip_id},
        )

    logger.info(f"Successfully duplicated trip {trip_id} into {new_trip_id}")
    return new_trip_id
