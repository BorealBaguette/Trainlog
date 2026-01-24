import logging

from py.sql import saveQuery
from src.paths import Path
from src.pg import get_or_create_pg_session
from src.sql.trips import insert_trip_query
from src.utils import mainConn, managed_cursor, pathConn

from .trip import Trip
from .utils import compare_trip

logger = logging.getLogger(__name__)


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
                "carbon": trip.carbon,
                "visibility": trip.visibility,
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
                    INSERT INTO trip ('username',
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
                        'ticket_id',
                        'visibility')
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING uid; \
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
                    trip.visibility,
                ),
            )
            # Retrieve the trip_id directly from the INSERT statement
            trip_id = cursor.fetchone()[0]

        # Prepare the path data with the obtained trip_id
        if isinstance(trip.path, Path):
            trip.path.set_trip_id(trip_id)
            path = trip.path
        else:
            path = Path(path=trip.path, trip_id=trip_id)

        # Use your existing saveQuery template for the path
        save_path_query = saveQuery.format(
            table="paths",
            keys="({})".format(", ".join(path.keys())),
            values=", ".join(["?"] * len(path.keys())),
        )

        pathConn.execute("BEGIN TRANSACTION")
        with managed_cursor(pathConn) as cursor:
            cursor.execute(save_path_query, path.values())

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
