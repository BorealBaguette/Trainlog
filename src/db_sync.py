import csv
import io
import json
import logging.config

from src.paths import coords_to_ewkt
from src.pg import get_or_create_pg_session, pg_session
from src.sqlite_legacy import mainConn, pathConn
from src.trips import Trip, compare_trip
from src.utils import authConn, managed_cursor, parse_date

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
    # Each group runs in its own transaction (committed independently) so the
    # one-shot server migration doesn't build a single enormous transaction
    # (paths alone is ~14GB of geometry).
    sync_trips_from_sqlite()
    sync_operators_from_sqlite()
    sync_aux_tables_from_sqlite()
    sync_trip_related_tables_from_sqlite()
    sync_reference_tables_from_sqlite()
    sync_paths_from_sqlite()
    sync_carbon()


def sync_carbon():
    """Recompute the carbon footprint for every trip.

    Carbon is not stored in SQLite (it is computed from the route geometry), so a
    fresh trips sync writes carbon = NULL for every row. This recomputes it from
    the paths now present in PG, and must therefore run after sync_paths.
    """
    logger.info("Backfilling carbon footprints in PostgreSQL...")
    # Imported lazily to avoid a src -> scripts import at module load.
    from scripts.backfill_carbon import backfill_carbon_for_all_trips

    backfill_carbon_for_all_trips()


def _clean_coord(value):
    """Coerce a possibly-dirty SQLite coordinate (stored as text in a FLOAT column)
    to a float. Handles European decimal commas ('12,25' -> 12.25) and the couple
    of corrupt values that carry both a period and a trailing comma fragment
    ('-8.433632,293' -> -8.433632)."""
    if value is None or value == "":
        return None
    s = str(value).strip().replace("−", "-")  # normalise unicode minus
    if "," in s and "." in s:
        s = s.split(",")[0]
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _copy_full_table(
    pg, sqlite_conn, table, *, drop_col=None, reset_serial=None, bool_cols=(),
    coord_cols=(),
):
    """Copy every row of a SQLite table into the identically-named PG table.

    Column names are read from SQLite and reused verbatim (quoted) so case is
    preserved (needed for the upper-case currency columns in `exchanges`).
    Existing PG rows are removed first, making this idempotent.

    - `drop_col`: exclude this column from the copy and let Postgres' SERIAL
      default generate it. Use for ids SQLite stores as meaningless/NULL (e.g.
      the fake `SERIAL` columns of fr24_usage/ai_usage) that nothing references.
    - `reset_serial`: after copying (preserving the id), realign this SERIAL
      sequence to MAX(id). Use for ids that ARE referenced elsewhere (e.g.
      tickets.uid, tags.uid) so they must be kept and future inserts not collide.
    - `bool_cols`: SQLite stores booleans as 0/1; coerce these columns to Python
      bool so they fit PG BOOLEAN columns.
    """
    with managed_cursor(sqlite_conn) as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        logger.info(f"Syncing {count} rows from SQLite table '{table}'")
        cursor.execute(f"SELECT * FROM {table}")
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()

    keep_idx = [i for i, c in enumerate(columns) if c != drop_col]
    keep_cols = [columns[i] for i in keep_idx]
    bool_idx = {i for i, c in enumerate(columns) if c in bool_cols}
    coord_idx = {i for i, c in enumerate(columns) if c in coord_cols}

    def conv(value, idx):
        if idx in bool_idx and value is not None:
            return bool(value)
        if idx in coord_idx:
            return _clean_coord(value)
        return value

    # Build converted rows, skipping any whose coordinate columns are missing /
    # unparseable (trash data we don't want to carry into PG's NOT NULL columns).
    converted = []
    skipped = 0
    for r in rows:
        values = tuple(conv(r[i], i) for i in keep_idx)
        if coord_idx and any(
            values[pos] is None for pos, i in enumerate(keep_idx) if i in coord_idx
        ):
            skipped += 1
            continue
        converted.append(values)
    if skipped:
        logger.warning(f"Skipped {skipped} '{table}' rows with invalid coordinates")

    raw = pg.connection().connection.cursor()
    raw.execute(f"DELETE FROM {table};")
    if converted:
        col_idents = ", ".join(f'"{c}"' for c in keep_cols)
        placeholders = ", ".join(["%s"] * len(keep_cols))
        raw.executemany(
            f"INSERT INTO {table} ({col_idents}) VALUES ({placeholders})",
            converted,
        )

    if reset_serial is not None:
        pg.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{reset_serial}'),"
            f" COALESCE((SELECT MAX({reset_serial}) FROM {table}), 0), true)"
        )


def sync_aux_tables_from_sqlite(pg_session=None):
    """Sync the stats/auxiliary tables (Phase 1) from SQLite main.db to PG."""
    logger.info("Syncing auxiliary tables from SQLite to PostgreSQL...")
    with get_or_create_pg_session(pg_session) as pg:
        _copy_full_table(pg, mainConn, "percents", drop_col="uid")
        _copy_full_table(pg, mainConn, "exchanges")
        _copy_full_table(pg, mainConn, "daily_active_users")
        _copy_full_table(pg, mainConn, "fr24_usage", drop_col="uid")
        _copy_full_table(pg, mainConn, "ai_usage", drop_col="uid")
    logger.info("Finished migrating auxiliary tables from sqlite to pg!")


def sync_trip_related_tables_from_sqlite(pg_session=None):
    """Sync trip-adjacent tables (Phase 2) from SQLite main.db to PG.

    tickets.uid and tags.uid are referenced (trips.ticket_id, tags_associations.tag_id)
    so their ids are preserved and the sequences realigned afterwards.
    """
    logger.info("Syncing trip-related tables from SQLite to PostgreSQL...")
    with get_or_create_pg_session(pg_session) as pg:
        # tags_associations FK-references tags/trips, so load tickets & tags first.
        _copy_full_table(
            pg, mainConn, "tickets", reset_serial="uid", bool_cols=("active",)
        )
        _copy_full_table(pg, mainConn, "tags", reset_serial="uid")
        _copy_full_table(pg, mainConn, "tags_associations")
        _copy_full_table(pg, mainConn, "gpx", reset_serial="uid")
        _copy_full_table(pg, mainConn, "ship_pictures", reset_serial="uid")
    logger.info("Finished migrating trip-related tables from sqlite to pg!")


def sync_paths_from_sqlite(pg_session=None):
    """Stream the (large) SQLite path.db `paths` table into the PostGIS `paths`
    table, converting each JSON [[lat,lng],...] route to a geometry. Streamed in
    batches via COPY to keep memory bounded; rows with empty/invalid coordinates
    are skipped.
    """
    logger.info("Syncing paths from SQLite to PostGIS...")
    with get_or_create_pg_session(pg_session) as pg:
        raw = pg.connection().connection.cursor()
        raw.execute("DELETE FROM paths;")

        with managed_cursor(pathConn) as cursor:
            cursor.execute("SELECT COUNT(*) FROM paths")
            total = cursor.fetchone()[0]
            logger.info(f"Syncing {total} paths from SQLite to PostGIS")
            cursor.execute("SELECT trip_id, path FROM paths")

            seen = inserted = skipped = 0
            while True:
                rows = cursor.fetchmany(2000)
                if not rows:
                    break
                buf = io.StringIO()
                writer = csv.writer(buf, delimiter="\t")
                batch_n = 0
                for row in rows:
                    seen += 1
                    try:
                        coords = json.loads(row["path"])
                    except (TypeError, ValueError):
                        skipped += 1
                        continue
                    ewkt = coords_to_ewkt(coords)
                    if ewkt is None:
                        skipped += 1
                        continue
                    writer.writerow([row["trip_id"], ewkt])
                    batch_n += 1
                if batch_n:
                    buf.seek(0)
                    raw.copy_expert(
                        "COPY paths (trip_id, geom) FROM STDIN WITH (FORMAT csv, DELIMITER E'\t')",
                        buf,
                    )
                    inserted += batch_n
                if seen % 50000 == 0:
                    logger.info(f"paths {seen}/{total} (inserted {inserted})")

    logger.info(f"Finished migrating paths: inserted {inserted}, skipped {skipped}")


def sync_reference_tables_from_sqlite(pg_session=None):
    """Sync reference/geo tables (Phase 3) from SQLite main.db to PG."""
    logger.info("Syncing reference tables from SQLite to PostgreSQL...")
    with get_or_create_pg_session(pg_session) as pg:
        _copy_full_table(pg, mainConn, "airports")
        _copy_full_table(pg, mainConn, "train_stations")
        _copy_full_table(
            pg, mainConn, "manual_stations", reset_serial="uid",
            coord_cols=("lat", "lng"),
        )
        _copy_full_table(pg, mainConn, "here_api_operators")
    logger.info("Finished migrating reference tables from sqlite to pg!")


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
        trip.departure_delay,
        trip.arrival_delay,
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
            departure_delay=row["departure_delay"],
            arrival_delay=row["arrival_delay"],
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
                visibility,
                departure_delay,
                arrival_delay
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
