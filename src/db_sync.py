import csv
import io
import logging
import logging.config

from src.pg import get_or_create_pg_session, pg_session
from src.trips import Trip, compare_trip, parse_date
from src.utils import get_user_id, mainConn, managed_cursor

logging.config.fileConfig("logging.conf", disable_existing_loggers=False)
logger = logging.getLogger(__name__)


def sync_db_from_sqlite():
    """
    Sync the PostgreSQL database with the SQLite database.
    """

    logger.info("Syncing SQLite database with PostgreSQL...")
    with pg_session() as pg:
        sync_trips_from_sqlite(pg)


def trip_to_csv(trip: Trip):
    items = [
        trip.trip_id,
        trip.user_id,
        trip.origin_station,
        trip.destination_station,
        trip.start_datetime,
        trip.end_datetime,
        trip.is_project,
        trip.utc_start_datetime,
        trip.utc_end_datetime,
        trip.estimated_trip_duration,
        trip.manual_trip_duration,
        trip.trip_length,
        trip.operator,
        trip.countries,
        trip.line_name,
        trip.created,
        trip.last_modified,
        trip.type,
        trip.material_type,
        trip.seat,
        trip.reg,
        trip.waypoints,
        trip.notes,
        trip.price,
        trip.currency,
        trip.ticket_id,
        trip.purchasing_date,
    ]
    return items


def sync_trips_from_sqlite(pg_session=None):
    logger.info("Syncing trips from SQLite to PostgreSQL...")

    # fetch all trips from sqlite
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT count(*) FROM trip")
        num_trips = cursor.fetchone()[0]
        logger.info(f"Syncing {num_trips} trips from SQLite to PostgreSQL")

        cursor.execute("SELECT * FROM trip ORDER BY uid")
        sqlite_trips = cursor.fetchall()

    csv_buf = io.StringIO()
    csv_writer = csv.writer(csv_buf, delimiter="\t", quoting=csv.QUOTE_MINIMAL)

    for i, row in enumerate(sqlite_trips):
        if i % 20000 == 0:
            logger.info(f"Converting trip {i}/{num_trips}")

        start_datetime = (
            row["start_datetime"] if row["start_datetime"] not in [-1, 1] else None
        )
        parsed_start_datetime = parse_date(start_datetime) if start_datetime else None
        end_datetime = (
            row["end_datetime"] if row["end_datetime"] not in [-1, 1] else None
        )
        parsed_end_datetime = parse_date(end_datetime) if end_datetime else None
        parsed_utc_start_datetime = (
            parse_date(row["utc_start_datetime"]) if row["utc_start_datetime"] else None
        )
        parsed_utc_end_datetime = (
            parse_date(row["utc_end_datetime"]) if row["utc_end_datetime"] else None
        )
        trip = Trip(
            trip_id=row["uid"],
            username=row["username"],
            user_id=get_user_id(row["username"]),
            origin_station=row["origin_station"],
            destination_station=row["destination_station"],
            start_datetime=parsed_start_datetime,
            end_datetime=parsed_end_datetime,
            trip_length=row["trip_length"],
            estimated_trip_duration=row["estimated_trip_duration"],
            operator=row["operator"],
            countries=row["countries"],
            manual_trip_duration=row["manual_trip_duration"],
            utc_start_datetime=parsed_utc_start_datetime,
            utc_end_datetime=parsed_utc_end_datetime,
            created=row["created"],
            last_modified=row["last_modified"],
            line_name=row["line_name"],
            type=row["type"],
            material_type=row["material_type"],
            seat=row["seat"],
            reg=row["reg"],
            waypoints=row["waypoints"],
            notes=row["notes"],
            price=row["price"] if row["price"] != "" else None,
            currency=row["currency"],
            purchasing_date=row["purchasing_date"]
            if row["purchasing_date"] != ""
            else None,
            ticket_id=row["ticket_id"] if row["ticket_id"] != "" else None,
            is_project=row["start_datetime"] == 1 or row["end_datetime"] == 1,
            path=None,  # not needed when inserting trips
        )
        csv_writer.writerow(trip_to_csv(trip))

    csv_buf.seek(0)

    with get_or_create_pg_session(pg_session) as pg:
        # remove existing trips from pg
        logger.info("Deleting existing trips in pg...")
        query = "DELETE FROM trips;"
        pg.execute(query)

        query = """
            COPY trips (
                trip_id,
                user_id,
                origin_station,
                destination_station,
                start_datetime,
                end_datetime,
                is_project,
                utc_start_datetime,
                utc_end_datetime,
                estimated_trip_duration,
                manual_trip_duration,
                trip_length,
                operator,
                countries,
                line_name,
                created,
                last_modified,
                trip_type,
                material_type,
                seat,
                reg,
                waypoints,
                notes,
                price,
                currency,
                ticket_id,
                purchase_date
            ) FROM STDIN WITH (
                FORMAT csv,
                DELIMITER E'\t',
                QUOTE '"'
            )
        """

        logger.info("Bulk inserting trips in pg...")
        cursor = pg.connection().connection.cursor()
        cursor.copy_expert(query, csv_buf)
    logger.info("Finished migrating trips from sqlite to pg!")


def compare_all_trips():
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT uid FROM trip ORDER BY uid")
        sqlite_trips = cursor.fetchall()
    sqlite_trips = [trip[0] for trip in sqlite_trips]

    with pg_session() as pg:
        query = "SELECT count(*) FROM trips"
        num_trips_pg = pg.execute(query).scalar()

        if num_trips_pg != len(sqlite_trips):
            msg = f"SQLite has {len(sqlite_trips)} trips but PG has {num_trips_pg}!"
            logger.error(msg)
            raise Exception(msg)

    try:
        for i, trip_id in enumerate(sqlite_trips):
            if i % 20000 == 0:
                logger.info(f"Checking consistency of trip {i}/{len(sqlite_trips)}")
            compare_trip(trip_id)
    except Exception:
        logger.error(f"Found exception while processing trip {trip_id}")
        raise
