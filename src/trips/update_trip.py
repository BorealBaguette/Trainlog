import datetime
import json
import logging

from flask import abort

from py.sql import getUserLines, updatePath, updateTripQuery
from py.utils import getCountriesFromPath
from src.pg import pg_session
from src.sql.trips import update_trip_query
from src.utils import (
    get_username,
    mainConn,
    managed_cursor,
    owner,
    pathConn,
    processDates,
)

from .trip import Trip
from .utils import compare_trip

logger = logging.getLogger(__name__)


def update_trip(trip_id: int, trip: Trip, formData=None, updateCreated=False):
    with pg_session() as pg:
        _update_trip_in_sqlite(formData, trip.last_modified, trip_id, updateCreated)
        print(trip.carbon)
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
                "carbon": trip.carbon,
                "visibility": trip.visibility if trip.visibility != "" else None,
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
        elif get_username() not in (row["username"], owner):
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

    if "visibility" in formData:
        visibility = formData["visibility"]
    else:
        visibility = None

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
        "purchasing_date": formData.get("purchasing_date"),
        "visibility": visibility
        if visibility != ""
        else None
        if formData["price"] != ""
        else None,
    }

    if updateCreated:
        updateData["created"] = datetime.datetime.now()

    if "estimated_trip_duration" in formData and "trip_length" in formData:
        updateData["countries"] = getCountriesFromPath(
            [{"lat": coord[0], "lng": coord[1]} for coord in path],
            formData["type"],
            json.loads(formData.get("details"))
            if formData.get("details") is not None
            else None,
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
