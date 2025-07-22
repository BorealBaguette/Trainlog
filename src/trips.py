import datetime
import json
import logging
import traceback

from flask import abort, request

from py.sql import deletePathQuery, getUserLines, saveQuery, updatePath, updateTripQuery
from py.utils import getCountriesFromPath
from src.paths import Path
from src.pg import get_or_create_pg_session, pg_session
from src.sql.trips import (
    delete_trip_query,
    duplicate_trip_query,
    insert_trip_query,
    update_trip_query,
    update_trip_type_query,
    update_ticket_null_query,
    attach_ticket_query
)
from src.utils import (
    get_user_id,
    getUser,
    mainConn,
    managed_cursor,
    owner,
    pathConn,
    processDates,
    sendOwnerEmail,
)

logger = logging.getLogger(__name__)


class Trip:
    def __init__(
        self,
        username,
        user_id,
        origin_station,
        destination_station,
        start_datetime,
        end_datetime,
        trip_length,
        estimated_trip_duration,
        operator,
        countries,
        manual_trip_duration,
        utc_start_datetime,
        utc_end_datetime,
        created,
        last_modified,
        line_name,
        type,
        material_type,
        seat,
        reg,
        waypoints,
        notes,
        price,
        currency,
        purchasing_date,
        ticket_id,
        path,
        is_project,
        trip_id=None,
    ):
        self.trip_id = trip_id
        self.username = username
        self.user_id = user_id
        self.origin_station = origin_station
        self.destination_station = destination_station
        self.start_datetime = start_datetime
        self.end_datetime = end_datetime
        self.trip_length = trip_length
        self.estimated_trip_duration = estimated_trip_duration
        self.manual_trip_duration = manual_trip_duration
        self.operator = operator
        self.countries = countries
        self.utc_start_datetime = utc_start_datetime
        self.utc_end_datetime = utc_end_datetime
        self.created = created
        self.last_modified = last_modified
        self.line_name = line_name
        self.type = type
        self.material_type = material_type
        self.seat = seat
        self.reg = reg
        self.waypoints = waypoints
        self.notes = notes
        self.price = price
        self.currency = currency
        self.purchasing_date = purchasing_date
        self.ticket_id = ticket_id
        self.is_project = is_project
        self.path = path

    def keys(self):
        return tuple(vars(self).keys())

    def values(self):
        return tuple(vars(self).values())


def create_trip(trip: Trip, pg_session=None):
    with get_or_create_pg_session(pg_session) as pg:
        if trip.trip_id is None:
            # need to create the trip in sqlite first
            trip.trip_id = _create_trip_in_sqlite(trip)

        pg.execute(
            insert_trip_query(),
            {
                "trip_id": trip.trip_id,
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
                "seat": trip.seat,
                "reg": trip.reg,
                "waypoints": trip.waypoints,
                "notes": trip.notes,
                "price": trip.price,
                "currency": trip.currency,
                "ticket_id": trip.ticket_id,
                "purchase_date": trip.purchasing_date,
            },
        )

    compare_trip(trip.trip_id)
    logger.info(f"Successfully created trip {trip.trip_id}")


def _create_trip_in_sqlite(trip: Trip):
    """
    Temporary function to write trips in sqlite
    Will be replaced by PG eventually
    """
    saveTripQuery = """
        INSERT INTO trip (
            'username',
            'origin_station',
            'destination_station',
            'start_datetime',
            'end_datetime',
            'trip_length',
            'estimated_trip_duration',
            'manual_trip_duration',
            'operator',
            'countries',
            'utc_start_datetime',
            'utc_end_datetime',
            'created',
            'last_modified',
            'line_name',
            'type',
            'material_type',
            'seat',
            'reg',
            'waypoints',
            'notes',
            'price',
            'currency',
            'purchasing_date',
            'ticket_id'
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        ) RETURNING uid;
    """
    if trip.start_datetime is None:
        start_datetime = 1 if trip.is_project else -1
    else:
        start_datetime = trip.start_datetime
    if trip.end_datetime is None:
        end_datetime = 1 if trip.is_project else -1
    else:
        end_datetime = trip.end_datetime

    try:
        # Begin transactions in both databases
        mainConn.execute("BEGIN TRANSACTION")
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                saveTripQuery,
                (
                    trip.username,
                    trip.origin_station,
                    trip.destination_station,
                    start_datetime,
                    end_datetime,
                    trip.trip_length,
                    trip.estimated_trip_duration,
                    trip.manual_trip_duration,
                    trip.operator,
                    trip.countries,
                    trip.utc_start_datetime,
                    trip.utc_end_datetime,
                    trip.created,
                    trip.last_modified,
                    trip.line_name,
                    trip.type,
                    trip.material_type,
                    trip.seat,
                    trip.reg,
                    trip.waypoints,
                    trip.notes,
                    trip.price,
                    trip.currency,
                    trip.purchasing_date,
                    trip.ticket_id,
                ),
            )
            # Retrieve the trip_id directly from the INSERT statement
            trip_id = cursor.fetchone()[0]

        # Prepare the path data with the obtained trip_id
        if isinstance(trip.path, Path):
            path = trip.path
        else:
            path = Path(path=trip.path, trip_id=trip_id)

        # Use your existing saveQuery template for the path
        savePathQuery = saveQuery.format(
            table="paths",
            keys="({})".format(", ".join(path.keys())),
            values=", ".join(["?"] * len(path.keys())),
        )

        pathConn.execute("BEGIN TRANSACTION")
        with managed_cursor(pathConn) as cursor:
            cursor.execute(savePathQuery, path.values())

        # Commit both transactions
        mainConn.commit()
        pathConn.commit()

        return trip_id
    except Exception as e:
        # Rollback both transactions in case of error
        mainConn.rollback()
        pathConn.rollback()
        # Optionally, log the error or handle it as needed
        raise e


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


def update_trip(trip_id: int, trip: Trip, formData=None, updateCreated=False):
    with pg_session() as pg:
        _update_trip_in_sqlite(formData, trip.last_modified, trip_id, updateCreated)
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
                "seat": trip.seat,
                "reg": trip.reg,
                "waypoints": trip.waypoints,
                "notes": trip.notes,
                "price": trip.price if trip.price != "" else None,
                "currency": trip.currency,
                "ticket_id": trip.ticket_id if trip.ticket_id != "" else None,
                "purchase_date": trip.purchasing_date,
            },
        )

    compare_trip(trip_id)
    logger.info(f"Successfully updated trip {trip_id}")


def _update_trip_in_sqlite(
    formData,
    last_modified,
    tripId=None,
    updateCreated=False,
):
    if tripId is None:
        tripId = formData["trip_id"]

    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "SELECT username FROM trip WHERE uid = :trip_id", {"trip_id": tripId}
        )
        row = cursor.fetchone()

        if row is None:
            abort(404)  # Trip does not exist
        elif getUser() not in (row["username"], owner):
            abort(404)  # Trip does not belong to the user

    formattedGetUserLines = getUserLines.format(trip_ids=tripId)
    with managed_cursor(pathConn) as cursor:
        pathResult = cursor.execute(formattedGetUserLines).fetchone()

    if "path" in formData.keys():
        path = [[coord["lat"], coord["lng"]] for coord in json.loads(formData["path"])]
    else:
        path = json.loads(pathResult["path"])

    limits = [
        {
            "lat": path[0][0],
            "lng": path[0][1],
        },
        {
            "lat": path[-1][0],
            "lng": path[-1][1],
        },
    ]

    (
        manual_trip_duration,
        start_datetime,
        end_datetime,
        utc_start_datetime,
        utc_end_datetime,
    ) = processDates(formData, limits)
    updateData = {
        "trip_id": tripId,
        "manual_trip_duration": manual_trip_duration,
        "origin_station": formData["origin_station"],
        "destination_station": formData["destination_station"],
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "utc_start_datetime": utc_start_datetime,
        "utc_end_datetime": utc_end_datetime,
        "operator": formData["operator"],
        "line_name": formData["lineName"],
        "material_type": formData["material_type"],
        "reg": formData["reg"],
        "seat": formData["seat"],
        "notes": formData["notes"],
        "last_modified": last_modified,
        "price": formData["price"],
        "currency": formData.get("currency") if formData["price"] != "" else None,
        "ticket_id": formData.get("ticket_id"),
        "purchasing_date": formData.get("purchasing_date")
        if formData["price"] != ""
        else None,
    }

    if updateCreated:
        updateData["created"] = datetime.datetime.now()

    if "estimated_trip_duration" in formData and "trip_length" in formData:
        updateData["countries"] = getCountriesFromPath(
            [{"lat": coord[0], "lng": coord[1]} for coord in path], formData["type"]
        )
        updateData["estimated_trip_duration"] = formData["estimated_trip_duration"]
        updateData["trip_length"] = formData["trip_length"]
    if "waypoints" in formData:
        updateData["waypoints"] = formData["waypoints"]

    formatted_values = [
        (value + " = :" + value) for value in updateData if value != "trip_id"
    ]
    formattedUpdateQuery = updateTripQuery.format(values=", ".join(formatted_values))

    with managed_cursor(mainConn) as cursor:
        cursor.execute(formattedUpdateQuery, {**updateData})
    if path:
        with managed_cursor(pathConn) as cursor:
            cursor.execute(updatePath, {"trip_id": int(tripId), "path": str(path)})
        pathConn.commit()
    mainConn.commit()


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


def update_trip_type(trip_id, new_type):
    with pg_session() as pg:
        update_trip_type_in_sqlite(trip_id, new_type)
        pg.execute(
            update_trip_type_query(), {"trip_id": trip_id, "trip_type": new_type}
        )


def update_trip_type_in_sqlite(trip_id, new_type):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "UPDATE trip SET type = :newType WHERE uid = :tripId",
            {"newType": new_type, "tripId": trip_id},
        )
    mainConn.commit()

def delete_ticket_from_db(username, ticket_id):
    try:
        trip_ids = []

        with managed_cursor(mainConn) as cursor:
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
        compare_trip(trip_id)

        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)


def attach_ticket_to_trips(username, ticket_id, trip_ids):
    try:
        placeholders = ", ".join(["?"] * len(trip_ids))

        with managed_cursor(mainConn) as cursor:
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
        compare_trip(trip_id)

        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)


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


def parse_date(date: str):
    try:
        return datetime.datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    try:
        return datetime.datetime.strptime(date, "%Y-%m-%d %H:%M:%S.%f")
    except Exception:
        pass
    try:
        return datetime.datetime.strptime(date, "%Y/%m/%d %H:%M:%S")
    except Exception:
        pass
    try:
        return datetime.datetime.strptime(date, "%d/%m/%Y %H:%M")
    except Exception:
        pass
    try:
        return datetime.datetime.strptime(date, "%Y-%m-%d")
    except Exception:
        logger.error(f"Date format not recognized: {date} ({type(date)})")
        raise


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
            Logged in user : {getUser()}<br>
            <br>
            Trace : <br>
            <br>
            {trace}
        """
        logger.error(msg)

        if "127.0.0.1" not in request.url and "localhost" not in request.url:
            msg = ""
            sendOwnerEmail("Error : " + str(e), msg)
