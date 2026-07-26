import logging

from flask import abort

from src.pg import pg_session
from src.sql.trips import delete_trip_query
from src.utils import get_username

logger = logging.getLogger(__name__)


def delete_trip(trip_id: int, username: str):
    with pg_session() as pg:
        # Ownership check
        row = pg.execute(
            "SELECT user_id FROM trips WHERE trip_id = :trip_id", {"trip_id": trip_id}
        ).fetchone()
        if row is None:
            abort(404)  # Trip does not exist
        if get_username(row["user_id"]) != username:
            abort(404)  # Trip exists but doesn't belong to the user

        pg.execute(delete_trip_query(), {"trip_id": trip_id})
        pg.execute(
            "DELETE FROM tags_associations WHERE trip_id = :trip_id",
            {"trip_id": trip_id},
        )
        pg.execute("DELETE FROM paths WHERE trip_id = :trip_id", {"trip_id": trip_id})

    logger.info(f"Successfully deleted trip {trip_id}")
