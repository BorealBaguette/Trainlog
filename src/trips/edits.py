from flask import abort

from src.consts import TripTypes
from src.pg import pg_session
from src.sql.trips import (
    attach_ticket_query,
    change_visibility_query,
    update_ticket_null_query,
    update_trip_type_query,
)
from src.utils import mainConn, managed_cursor

from .utils import compare_trip


def attach_ticket_to_trips(username, ticket_id, trip_ids):
    try:
        placeholders = ", ".join(["?"] * len(trip_ids))

        with managed_cursor(mainConn) as cursor:
            # Check ticket ownership
            cursor.execute(
                "SELECT 1 FROM tickets WHERE username = ? AND uid = ?",
                (username, ticket_id),
            )
            if cursor.fetchone() is None:
                abort(401)

            # Check all trip ownership
            cursor.execute(
                f"""
                SELECT COUNT(*) as c FROM trip 
                WHERE username = ? AND uid IN ({placeholders})
                """,
                [username] + trip_ids,
            )
            count = cursor.fetchone()["c"]
            if count != len(trip_ids):
                abort(401)

            cursor.execute(
                f"""
                UPDATE trip SET ticket_id = ? 
                WHERE username = ? AND uid IN ({placeholders})
                """,
                [ticket_id, username] + trip_ids,
            )

        with pg_session() as pg:
            for trip_id in trip_ids:
                pg.execute(
                    attach_ticket_query(), {"trip_id": trip_id, "ticket_id": ticket_id}
                )
        for trip_id in trip_ids:
            compare_trip(trip_id)

        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)


def change_trips_visibility(username, visibility, trip_ids):
    try:
        placeholders = ", ".join(["?"] * len(trip_ids))

        if visibility not in ("public", "friends", "private"):
            abort(401)

        with managed_cursor(mainConn) as cursor:
            # Check all trip ownership
            cursor.execute(
                f"""
                SELECT COUNT(*) as c FROM trip 
                WHERE username = ? AND uid IN ({placeholders})
                """,
                [username] + trip_ids,
            )
            count = cursor.fetchone()["c"]
            if count != len(trip_ids):
                abort(401)

            cursor.execute(
                f"""
                UPDATE trip SET visibility = ? 
                WHERE username = ? AND uid IN ({placeholders})
                """,
                [visibility, username] + trip_ids,
            )

        with pg_session() as pg:
            for trip_id in trip_ids:
                pg.execute(
                    change_visibility_query(),
                    {"trip_id": trip_id, "visibility": visibility},
                )
        for trip_id in trip_ids:
            compare_trip(trip_id)

        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)


def update_trip_type(trip_id, new_type: TripTypes):
    with pg_session() as pg:
        _update_trip_type_in_sqlite(trip_id, new_type)
        pg.execute(
            update_trip_type_query(), {"trip_id": trip_id, "trip_type": new_type.value}
        )


def _update_trip_type_in_sqlite(trip_id, new_type: TripTypes):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "UPDATE trip SET type = :newType WHERE uid = :tripId",
            {"newType": new_type.value, "tripId": trip_id},
        )
    mainConn.commit()


def delete_ticket_from_db(username, ticket_id):
    try:
        trip_ids = []

        with managed_cursor(mainConn) as cursor:
            # Check ticket ownership
            cursor.execute(
                "SELECT 1 FROM tickets WHERE username = ? AND uid = ?",
                (username, ticket_id),
            )
            if cursor.fetchone() is None:
                abort(401)

            # Check trip ownership
            cursor.execute(
                "SELECT uid FROM trip WHERE username = ? AND ticket_id = ?",
                (username, ticket_id),
            )
            trip_ids = [row["uid"] for row in cursor.fetchall()]

            cursor.execute(
                "UPDATE trip SET ticket_id = NULL WHERE username = ? AND ticket_id = ?",
                (username, ticket_id),
            )
            cursor.execute(
                "DELETE FROM tickets WHERE username = ? AND uid = ?",
                (username, ticket_id),
            )

        with pg_session() as pg:
            for trip_id in trip_ids:
                pg.execute(update_ticket_null_query(), {"trip_id": trip_id})
        for trip_id in trip_ids:
            compare_trip(trip_id)

        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)
