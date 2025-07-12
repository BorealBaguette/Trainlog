from paths import Path
from py.sql import saveQuery
from src.pg import pg_session
from src.sql.trips import insert_trip
from utils import mainConn, managed_cursor, pathConn


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
    ):
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
        self.path = path

    def keys(self):
        return tuple(vars(self).keys())

    def values(self):
        return tuple(vars(self).values())


def create_trip_in_db(trip: Trip):
    with pg_session() as pg:
        _create_trip_in_sqlite(trip)
        pg.execute(
            insert_trip(),
            {
                "user_id": trip.user_id,
                "origin_station": trip.origin_station,
                "start_datetime": trip.start_datetime,
                "end_datetime": trip.end_datetime,
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


def _create_trip_in_sqlite(trip: Trip):
    """
    Temporary function to write trips in sqlite
    Will be replaced by PG eventually
    """
    saveTripQuery = (
        saveQuery.format(
            table="trip", keys=trip.keys(), values=", ".join(("?",) * len(trip.keys()))
        )
        + " RETURNING uid;"
    )

    try:
        # Begin transactions in both databases
        mainConn.execute("BEGIN TRANSACTION")
        with managed_cursor(mainConn) as cursor:
            cursor.execute(saveTripQuery, trip.values())
            # Retrieve the trip_id directly from the INSERT statement
            trip_id = cursor.fetchone()[0]

        # Prepare the path data with the obtained trip_id
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
    except Exception as e:
        # Rollback both transactions in case of error
        mainConn.rollback()
        pathConn.rollback()
        # Optionally, log the error or handle it as needed
        raise e
