import logging

from src.pg import pg_session
from src.sql.trips import duplicate_trip_query
from src.utils import mainConn, managed_cursor, pathConn

from .utils import compare_trip

logger = logging.getLogger(__name__)


def duplicate_trip(trip_id: int):
    with pg_session() as pg:
        new_trip_id = _duplicate_trip_in_sqlite(trip_id)
        pg.execute(
            duplicate_trip_query(),
            {
                "trip_id": trip_id,
                "new_trip_id": new_trip_id,
            },
        )

    compare_trip(trip_id)
    compare_trip(new_trip_id)
    logger.info(f"Successfully duplicated trip {trip_id} into {new_trip_id}")
    return new_trip_id


def _duplicate_trip_in_sqlite(trip_id):
    with managed_cursor(mainConn) as cursor:
        # Fetch the column names
        cursor.execute("PRAGMA table_info(trip)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns if col[1] != "uid"]

        # Fetch the row to duplicate
        cursor.execute("SELECT * FROM trip WHERE uid = ?", (trip_id,))
        row_to_duplicate = cursor.fetchone()

        if row_to_duplicate:
            # Create a new row with the new UID
            row_to_duplicate = list(row_to_duplicate)
            row_to_duplicate.pop(0)

            # Construct the INSERT statement dynamically
            columns_str = ", ".join(column_names)
            placeholders = ", ".join(["?"] * len(column_names))
            insert_query = f"INSERT INTO trip ({columns_str}) VALUES ({placeholders})"
            cursor.execute(insert_query, row_to_duplicate)
            new_trip_id = cursor.lastrowid
    with managed_cursor(pathConn) as cursor:
        cursor.execute("select path from paths where trip_id = ?", (trip_id,))
        path_to_duplicate = cursor.fetchone()["path"]
        cursor.execute(
            "insert into paths (trip_id, path) VALUES (?, ?)",
            (new_trip_id, path_to_duplicate),
        )
    mainConn.commit()
    pathConn.commit()
    return new_trip_id
