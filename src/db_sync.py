import csv
import io
import json
import logging
import logging.config
import sqlite3

from src.pg import get_or_create_pg_session, pg_session
from src.trips import Trip, compare_trip, parse_date
from src.utils import get_user_id, mainConn, pathConn, managed_cursor
from sqlalchemy import text

logging.config.fileConfig("logging.conf", disable_existing_loggers=False)
logger = logging.getLogger(__name__)


def sync_db_from_sqlite():
    """
    Sync the PostgreSQL database with the SQLite database.
    """

    logger.info("Syncing SQLite database with PostgreSQL...")
    with pg_session() as pg:
        sync_trips_from_sqlite(pg)
        sync_paths_from_sqlite(pg)


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


def sync_paths_from_sqlite(pg_session=None):
    """
    Sync paths from paths.db SQLite to PostgreSQL paths table.
    """
    logger.info("Syncing paths from SQLite to PostgreSQL...")
    
    try:
        # Get all paths from SQLite
        cursor = pathConn.cursor()
        cursor.execute("SELECT count(*) FROM paths")
        num_paths = cursor.fetchone()[0]
        logger.info(f"Syncing {num_paths} paths from SQLite to PostgreSQL")
        
        cursor.execute("SELECT trip_id, path FROM paths ORDER BY trip_id")
        sqlite_paths = cursor.fetchall()
        
        with get_or_create_pg_session(pg_session) as pg:
            # Delete existing paths
            logger.info("Deleting existing paths in pg...")
            pg.execute("DELETE FROM paths;")
            
            # Prepare batch insert
            batch_size = 1000
            batch = []
            
            for i, row in enumerate(sqlite_paths):
                if i % 10000 == 0:
                    logger.info(f"Converting path {i}/{num_paths}")
                
                trip_id = row['trip_id']
                path_str = row['path']
                
                # Convert [[X,Y],[X,Y]] string to WKT LINESTRING
                try:
                    # Parse the string representation of the coordinates
                    coordinates = json.loads(path_str)
                    
                    if coordinates:
                        if len(coordinates) >= 2:
                            # Create WKT LINESTRING format
                            wkt_coords = ', '.join([f"{lon} {lat}" for lon, lat in coordinates])
                            wkt = f"LINESTRING({wkt_coords})"
                        elif len(coordinates) == 1:
                            lon, lat = coordinates[0]
                            wkt = f"POINT({lon} {lat})"
                        else:
                            logger.warning(f"Trip {trip_id} has empty coordinate list: {path_str}")
                            continue

                        batch.append((trip_id, wkt))

                        # Insert in batches
                        if len(batch) >= batch_size:
                            insert_paths_batch(pg, batch)
                            batch = []
                    else:
                        logger.warning(f"Trip {trip_id} has invalid path: {path_str}")

                        
                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(f"Failed to parse path for trip {trip_id}: {e}")
                    continue
            
            # Insert remaining paths
            if batch:
                insert_paths_batch(pg, batch)
                
            pg.commit()
            
    finally:
        pathConn.close()
    
    logger.info("Finished migrating paths from sqlite to pg!")


def insert_paths_batch(pg, batch):
    """
    Insert a batch of paths into PostgreSQL using SQLAlchemy and PostGIS.
    """
    if not batch:
        return

    # Build the parameter list as dicts for SQLAlchemy
    param_dicts = [
        {"trip_id": trip_id, "wkt": wkt}
        for trip_id, wkt in batch
    ]

    query = text("""
        INSERT INTO paths (trip_id, path)
        VALUES (:trip_id, ST_GeomFromText(:wkt, 4326))
        ON CONFLICT (trip_id) DO UPDATE
        SET path = EXCLUDED.path
    """)

    pg.execute(query, param_dicts)


def compare_all_trips():
    # Fetch trip IDs from SQLite
    with managed_cursor(mainConn) as cursor:
        cursor.execute("SELECT uid FROM trip ORDER BY uid")
        sqlite_trips = {row[0] for row in cursor.fetchall()}

    # Fetch trip IDs from PostgreSQL
    with pg_session() as pg:
        pg_trips = {row[0] for row in pg.execute("SELECT trip_id FROM trips ORDER BY trip_id").fetchall()}

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


def compare_all_paths():
    """
    Compare paths between SQLite and PostgreSQL to ensure consistency.
    """
    logger.info("Comparing paths between SQLite and PostgreSQL...")
    
    
    try:
        # Get path trip IDs from SQLite
        cursor = pathConn.cursor()
        cursor.execute("SELECT trip_id FROM paths ORDER BY trip_id")
        sqlite_path_ids = {row[0] for row in cursor.fetchall()}
        
        # Get path trip IDs from PostgreSQL
        with pg_session() as pg:
            pg_path_ids = {row[0] for row in pg.execute("SELECT trip_id FROM paths ORDER BY trip_id").fetchall()}
        
        # Compare counts
        if len(sqlite_path_ids) != len(pg_path_ids):
            only_in_sqlite = sqlite_path_ids - pg_path_ids
            only_in_pg = pg_path_ids - sqlite_path_ids
            
            msg = (
                f"Mismatch in path counts! "
                f"SQLite has {len(sqlite_path_ids)} paths, PG has {len(pg_path_ids)} paths.\n"
                f"Paths only in SQLite: {sorted(only_in_sqlite)}\n"
                f"Paths only in PG: {sorted(only_in_pg)}"
            )
            logger.error(msg)
            raise Exception(msg)
        
        logger.info(f"Path counts match: {len(sqlite_path_ids)} paths in both databases")
        
    finally:
        pathConn.close()