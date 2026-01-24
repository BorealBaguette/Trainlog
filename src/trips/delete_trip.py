import logging

from flask import abort

from py.sql import deletePathQuery
from src.pg import pg_session
from src.sql.trips import delete_trip_query
from src.utils import mainConn, managed_cursor, pathConn

from .utils import compare_trip

logger = logging.getLogger(__name__)


def delete_trip(trip_id: int, username: str):
    with pg_session() as pg:
        _delete_trip_in_sqlite(username, trip_id)
        pg.execute(delete_trip_query(), {"trip_id": trip_id})

    compare_trip(trip_id)
    logger.info(f"Successfully deleted trip {trip_id}")


def _delete_trip_in_sqlite(username, tripId):
    with managed_cursor(mainConn) as cursor:
        # Check ownership
        cursor.execute(
            "SELECT username FROM trip WHERE uid = :trip_id",
            {"trip_id": tripId},
        )
        row = cursor.fetchone()

        if row is None:
            abort(404)  # Trip does not exist
        elif row["username"] != username:
            abort(404)  # Trip exists but doesn't belong to the user

        # Delete only if the trip exists and belongs to the user
        cursor.execute("DELETE FROM trip WHERE uid = :trip_id", {"trip_id": tripId})
        cursor.execute(
            "DELETE FROM tags_associations WHERE trip_id = :trip_id",
            {"trip_id": tripId},
        )

    with managed_cursor(pathConn) as cursor:
        cursor.execute(deletePathQuery, {"trip_id": tripId})
    mainConn.commit()
    pathConn.commit()
