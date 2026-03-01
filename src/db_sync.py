import csv
import io
import logging.config

from src.pg import get_or_create_pg_session, pg_session
from src.trips import Trip, compare_trip
from src.utils import authConn, mainConn, managed_cursor, parse_date

logging.config.fileConfig("logging.conf", disable_existing_loggers=False)
logger = logging.getLogger(__name__)


def get_user_id(username):
    with managed_cursor(authConn) as cursor:
        cursor.execute(
            """
            SELECT uid FROM user
            WHERE username = ?
        """,
            (username,),
        )
        row = cursor.fetchone()
    if row:
        return row[0]
    return None


def sync_db_from_sqlite():
    """
    Sync the PostgreSQL database with the SQLite database.
    """

    logger.info("Syncing SQLite database with PostgreSQL...")
    with pg_session() as pg:
        sync_trips_from_sqlite(pg)
        sync_operators_from_sqlite(pg)


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
        trip.material_type_advanced,
        trip.seat,
        trip.reg,
        trip.waypoints,
        trip.notes,
        trip.price,
        trip.currency,
        trip.ticket_id,
        trip.purchasing_date,
        trip.visibility,
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
            material_type_advanced=row["material_type_advanced"],
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
            visibility=row["visibility"],
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
                material_type_advanced,
                seat,
                reg,
                waypoints,
                notes,
                price,
                currency,
                ticket_id,
                purchase_date,
                visibility
            ) FROM STDIN WITH (
                FORMAT csv,
                DELIMITER E'\t',
                QUOTE '"'
            )
        """

        logger.info("Bulk inserting trips in pg...")
        cursor = pg.connection().connection.cursor()
        cursor.copy_expert(query, csv_buf)

        # reset ID sequence so future rows line up with SQLite
        pg.execute(
            "SELECT setval(pg_get_serial_sequence('trips', 'trip_id'),COALESCE((SELECT MAX(trip_id) FROM trips), 0),true)"
        )
    logger.info("Finished migrating trips from sqlite to pg!")


def compare_all_trips():
    # Fetch trip IDs from SQLite
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT uid FROM trip ORDER BY uid")
        sqlite_trips = {row[0] for row in cursor.fetchall()}

    # Fetch trip IDs from PostgreSQL
    with pg_session() as pg:
        pg_trips = {
            row[0]
            for row in pg.execute(
                "SELECT trip_id FROM trips ORDER BY trip_id"
            ).fetchall()
        }

    # Compare the counts
    if len(sqlite_trips) != len(pg_trips):
        only_in_sqlite = sqlite_trips - pg_trips
        only_in_pg = pg_trips - sqlite_trips

        msg = (
            f"Mismatch in trip counts! "
            f"SQLite has {len(sqlite_trips)} trips, PG has {len(pg_trips)} trips.\n"
            f"Trips only in SQLite: {sorted(only_in_sqlite)}\n"
            f"Trips only in PG: {sorted(only_in_pg)}"
        )
        logger.error(msg)
        raise Exception(msg)

    # If counts match, do full comparison
    try:
        for i, trip_id in enumerate(sorted(sqlite_trips)):
            if i % 20000 == 0:
                logger.info(f"Checking consistency of trip {i}/{len(sqlite_trips)}")
            compare_trip(trip_id)
    except Exception:
        logger.error(f"Found exception while processing trip {trip_id}")
        raise


def _insert_operator_into_pg(pg_session, operator):
    pg_session.execute(
        "INSERT INTO operators (operator_id, operator_type, short_name, long_name) VALUES (:id, :type, :short_name, :long_name)",
        {
            "id": operator["uid"],
            "type": operator["operator_type"],
            "short_name": operator["short_name"],
            "long_name": operator["long_name"],
        },
    )


def _insert_operator_logo_into_pg(pg_session, operator):
    pg_session.execute(
        "INSERT INTO operator_logos (uid, operator_id, logo_url, effective_date) VALUES (:uid, :operator_id, :logo_url, :effective_date)",
        {
            "uid": operator["uid"],
            "operator_id": operator["operator_id"],
            "logo_url": operator["logo_url"],
            "effective_date": operator["effective_date"],
        },
    )


def sync_operators_from_sqlite(pg_session=None):
    logger.info("Step 1/2: Syncing operators from SQLite to PostgreSQL...")

    with managed_cursor(mainConn) as main_cursor:
        main_cursor.execute("SELECT count(*) FROM operators")
        num_operators = main_cursor.fetchone()[0]
        logger.info(f"Syncing {num_operators} operators from SQLite to PostgreSQL")
        all_operators = main_cursor.execute(
            "SELECT * FROM operators ORDER BY uid"
        ).fetchall()

    with get_or_create_pg_session(pg_session) as pg:
        pg.execute("DELETE FROM operators;")
        for i, row in enumerate(all_operators):
            _insert_operator_into_pg(pg, row)

        # reset ID sequence so future rows line up with SQLite
        pg.execute(
            "SELECT setval(pg_get_serial_sequence('operators', 'operator_id'),COALESCE((SELECT MAX(operator_id) FROM operators), 0),true)"
        )

    logger.info("Step 2/2: Syncing operator logos from SQLite to PostgreSQL...")

    with managed_cursor(mainConn) as main_cursor:
        main_cursor.execute("SELECT count(*) from operator_logos")
        num_logos = main_cursor.fetchone()[0]
        logger.info(f"Syncing {num_logos} operator logos from SQLite to PostgreSQL")
        operator_logos = main_cursor.execute("SELECT * FROM operator_logos").fetchall()

    with get_or_create_pg_session(pg_session) as pg:
        pg.execute("DELETE FROM operator_logos;")
        for i, row in enumerate(operator_logos):
            _insert_operator_logo_into_pg(pg, row)

        # reset ID sequence so future rows line up with SQLite
        pg.execute(
            "SELECT setval(pg_get_serial_sequence('operator_logos', 'uid'),COALESCE((SELECT MAX(uid) FROM operator_logos), 0),true)"
        )

    logger.info("Finished migrating operators from sqlite to pg!")
