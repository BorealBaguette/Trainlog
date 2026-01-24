import datetime
import logging
import traceback

from flask import request

from src.pg import pg_session
from src.sql.trips import get_current_trip_query
from src.utils import (
    get_user_id,
    get_username,
    mainConn,
    managed_cursor,
    parse_date,
    sendOwnerEmail,
)

from .trip import Trip

logger = logging.getLogger(__name__)


def ensure_values_equal(sqlite_trip, pg_trip, property_name):
    sqlite_val = sqlite_trip[property_name]
    pg_val = pg_trip[property_name]

    if sqlite_val is None and pg_val is None:
        values_are_equal = True
    elif property_name in [
        "start_datetime",
        "utc_start_datetime",
        "created",
        "last_modified",
        "purchase_date",
    ]:
        values_are_equal = abs(pg_val - sqlite_val) <= datetime.timedelta(seconds=1)
    else:
        values_are_equal = pg_val == sqlite_val

    if not values_are_equal:
        msg = (
            f"Trip {sqlite_trip['trip_id']} has different values on {property_name}: "
            f"{sqlite_val} (sqlite) vs {pg_val} (pg)"
        )
        logger.error(msg)
        raise Exception(msg)


def compare_trip(trip_id: int):
    """
    Check that the given trip has the same data in sqlite and pg
    """
    try:
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                "SELECT * FROM trip WHERE uid = :trip_id", {"trip_id": trip_id}
            )
            sqlite_trip = cursor.fetchone()
            sqlite_trip = dict(sqlite_trip) if sqlite_trip else None

        with pg_session() as pg:
            pg_trip = pg.execute(
                "SELECT * FROM trips WHERE trip_id = :trip_id", {"trip_id": trip_id}
            ).fetchone()

        if sqlite_trip is None and pg_trip is None:
            return
        if sqlite_trip is None or pg_trip is None:
            msg = (
                f"Trip {trip_id} exists in one db but not the other: "
                f"{sqlite_trip} (sqlite) vs {pg_trip} (pg)"
            )
            logger.error(msg)
            raise Exception(msg)

        sqlite_trip["trip_id"] = sqlite_trip["uid"]
        sqlite_trip["user_id"] = get_user_id(sqlite_trip["username"])
        sqlite_trip["is_project"] = (
            sqlite_trip["start_datetime"] == 1 or sqlite_trip["end_datetime"] == 1
        )
        if sqlite_trip["start_datetime"] in [-1, 1]:
            sqlite_trip["start_datetime"] = None
        else:
            sqlite_trip["start_datetime"] = parse_date(sqlite_trip["start_datetime"])
        if sqlite_trip["end_datetime"] in [-1, 1]:
            sqlite_trip["end_datetime"] = None
        else:
            sqlite_trip["end_datetime"] = parse_date(sqlite_trip["end_datetime"])
        if sqlite_trip["utc_start_datetime"] is not None:
            sqlite_trip["utc_start_datetime"] = parse_date(
                sqlite_trip["utc_start_datetime"]
            )
        if sqlite_trip["utc_end_datetime"] is not None:
            sqlite_trip["utc_end_datetime"] = parse_date(
                sqlite_trip["utc_end_datetime"]
            )
        if sqlite_trip["operator"] == "":
            sqlite_trip["operator"] = None
        if sqlite_trip["operator"] is not None:
            sqlite_trip["operator"] = str(sqlite_trip["operator"])
        if sqlite_trip["line_name"] == "":
            sqlite_trip["line_name"] = None
        if sqlite_trip["created"] is not None:
            sqlite_trip["created"] = parse_date(sqlite_trip["created"])
        if sqlite_trip["last_modified"] is not None:
            sqlite_trip["last_modified"] = parse_date(sqlite_trip["last_modified"])
        sqlite_trip["trip_type"] = sqlite_trip["type"]
        if sqlite_trip["material_type"] == "":
            sqlite_trip["material_type"] = None
        if sqlite_trip["seat"] == "":
            sqlite_trip["seat"] = None
        if sqlite_trip["reg"] == "":
            sqlite_trip["reg"] = None
        if sqlite_trip["waypoints"] == "":
            sqlite_trip["waypoints"] = None
        if sqlite_trip["notes"] == "":
            sqlite_trip["notes"] = None
        if sqlite_trip["price"] == "":
            sqlite_trip["price"] = None
        if sqlite_trip["ticket_id"] == "":
            sqlite_trip["ticket_id"] = None
        sqlite_trip["purchase_date"] = sqlite_trip["purchasing_date"]
        if sqlite_trip["purchase_date"] == "":
            sqlite_trip["purchase_date"] = None
        if sqlite_trip["purchase_date"] is not None:
            sqlite_trip["purchase_date"] = parse_date(sqlite_trip["purchase_date"])
        ensure_values_equal(sqlite_trip, pg_trip, "user_id")
        ensure_values_equal(sqlite_trip, pg_trip, "origin_station")
        ensure_values_equal(sqlite_trip, pg_trip, "destination_station")
        ensure_values_equal(sqlite_trip, pg_trip, "start_datetime")
        ensure_values_equal(sqlite_trip, pg_trip, "end_datetime")
        ensure_values_equal(sqlite_trip, pg_trip, "is_project")
        ensure_values_equal(sqlite_trip, pg_trip, "utc_start_datetime")
        ensure_values_equal(sqlite_trip, pg_trip, "utc_end_datetime")
        ensure_values_equal(sqlite_trip, pg_trip, "estimated_trip_duration")
        ensure_values_equal(sqlite_trip, pg_trip, "manual_trip_duration")
        ensure_values_equal(sqlite_trip, pg_trip, "trip_length")
        ensure_values_equal(sqlite_trip, pg_trip, "operator")
        ensure_values_equal(sqlite_trip, pg_trip, "countries")
        ensure_values_equal(sqlite_trip, pg_trip, "line_name")
        ensure_values_equal(sqlite_trip, pg_trip, "created")
        ensure_values_equal(sqlite_trip, pg_trip, "last_modified")
        ensure_values_equal(sqlite_trip, pg_trip, "trip_type")
        ensure_values_equal(sqlite_trip, pg_trip, "material_type")
        ensure_values_equal(sqlite_trip, pg_trip, "seat")
        ensure_values_equal(sqlite_trip, pg_trip, "reg")
        ensure_values_equal(sqlite_trip, pg_trip, "waypoints")
        ensure_values_equal(sqlite_trip, pg_trip, "notes")
        ensure_values_equal(sqlite_trip, pg_trip, "price")
        ensure_values_equal(sqlite_trip, pg_trip, "currency")
        ensure_values_equal(sqlite_trip, pg_trip, "ticket_id")
        ensure_values_equal(sqlite_trip, pg_trip, "purchase_date")
    except Exception as e:
        logger.exception(e)
        trace = traceback.format_exc().replace("\n", "<br>")
        msg = f"""
            Trip {trip_id} has drifted between SQLite and PG!<br>
            URL : {request.url} <br>
            <br>
            Logged in user : {get_username()}<br>
            <br>
            Trace : <br>
            <br>
            {trace}
        """
        logger.error(msg)

        if "127.0.0.1" not in request.url and "localhost" not in request.url:
            msg = ""
            sendOwnerEmail("Error : " + str(e), msg)


def get_current_trip_id() -> Trip | None:
    with pg_session() as pg:
        trip = pg.execute(
            get_current_trip_query(), {"user_id": get_user_id()}
        ).fetchone()
        return trip["trip_id"] if trip is not None else None
