import logging

from src.pg import pg_session
from src.sql.trips import duplicate_trip_new_user_query, duplicate_trip_query
from src.utils import mainConn, managed_cursor, pathConn

from .utils import compare_trip

logger = logging.getLogger(__name__)


def duplicate_trips(trip_ids: list[int], owner_id: int, owner_username: str) -> list[int]:
    """
    Duplicates a list of trip ids to a new owner
    """
    new_trip_ids = []
    for trip_id in trip_ids:
        new_trip_ids.append(_duplicate_trip(trip_id, owner_id, owner_username))

    return new_trip_ids


# This would be nice to merge with the function below but that should be checked
def _duplicate_trip(trip_id: int, owner_id: int, owner_username: str) -> int:
    with pg_session() as pg:
        new_trip_id = _duplicate_trip_in_sqlite(trip_id, owner_username)
        pg.execute(
            duplicate_trip_new_user_query(),
            {
                "trip_id": trip_id,
                "new_trip_id": new_trip_id,
                "new_user_id": owner_id
            },
        )

    compare_trip(trip_id)
    compare_trip(new_trip_id)
    logger.info(f"Successfully duplicated trip {trip_id} into {new_trip_id}")
    return new_trip_id


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


def _duplicate_trip_in_sqlite(trip_id, new_owner: str | None = None):
    with managed_cursor(mainConn) as cursor:
        # Fetch the column names
        cursor.execute("PRAGMA table_info(trip)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns if col[1] != "uid"]

        # Fetch the row to duplicate
        cursor.execute("SELECT * FROM trip WHERE uid = ?", (trip_id,))
        row_to_duplicate = cursor.fetchone()

        if row_to_duplicate:
            # Handling edgecase where someone has a different field set to
            # their name and that is updated leading to duplicate on their
            # account
            row_to_duplicate = dict(row_to_duplicate)
            if new_owner:
                row_to_duplicate['username'] = new_owner
            # Create a new row with the new UID
            row_to_duplicate = list(row_to_duplicate.values())

            row_to_duplicate.pop(0)

            # Construct the INSERT statement dynamically
            columns_str = ", ".join(column_names)
            placeholders = ", ".join(["?"] * len(column_names))
            insert_query = f"INSERT INTO trip ({columns_str}) VALUES ({placeholders})"
            cursor.execute(insert_query, row_to_duplicate)
            new_trip_id = cursor.lastrowid
            cursor.execute(
                "UPDATE trip SET departure_delay = NULL, arrival_delay = NULL WHERE uid = ?",
                (new_trip_id,),
            )
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
