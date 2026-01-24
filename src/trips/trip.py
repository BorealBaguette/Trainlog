from src.carbon import calculate_carbon_footprint_for_trip
from src.utils import get_username, managed_cursor, pathConn


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
        visibility=None,
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
        self.carbon = (
            calculate_carbon_footprint_for_trip(vars(self), path) if path else None
        )
        self.visibility = visibility

    def keys(self):
        return tuple(vars(self).keys())

    def values(self):
        return tuple(vars(self).values())

    @staticmethod
    def from_pg(trip):
        with managed_cursor(pathConn) as cursor:
            path = cursor.execute(
                "select path from paths where trip_id = ?", trip.trip_id
            ).fetchone()["path"]
        return Trip(
            get_username(trip["user_id"]),
            trip["user_id"],
            trip["origin_station"],
            trip["destination_station"],
            trip["start_datetime"],
            trip["end_datetime"],
            trip["trip_length"],
            trip["estimated_trip_duration"],
            trip["operator"],
            trip["countries"],
            trip["manual_trip_duration"],
            trip["utc_start_datetime"],
            trip["utc_end_datetime"],
            trip["created"],
            trip["last_modified"],
            trip["line_name"],
            trip["trip_type"],
            trip["material_type"],
            trip["seat"],
            trip["reg"],
            trip["waypoints"],
            trip["notes"],
            trip["price"],
            trip["currency"],
            trip["purchasing_date"],
            trip["ticket_id"],
            path,
            trip["is_project"],
            trip["trip_id"],
            trip["visibility"],
        )
