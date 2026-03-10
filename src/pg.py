import logging
import os
import re
import threading
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import sql
from src.consts import Env

logger = logging.getLogger(__name__)
threadlocal = threading.local()

# Global variables - will be initialized after fork
pg_session_engine = None
Session = None
_setup_complete = False


def get_db_connection_string():
    """
    Get db credentials from environment variables

    Raise an exception if they can't be found
    """
    pg_host = os.environ["POSTGRES_HOST"]
    pg_port = os.environ["POSTGRES_PORT"]
    pg_db = os.environ["POSTGRES_DB"]
    pg_user = os.environ["POSTGRES_USER"]
    pg_password = os.environ["POSTGRES_PASSWORD"]

    return f"postgresql+psycopg2://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}"


def init_db_engine():
    """
    Initialize the database engine and session maker.
    This should be called after forking to ensure each worker has its own connection pool.
    """
    global pg_session_engine, Session
    
    if pg_session_engine is None:
        logger.info(f"Initializing database engine for process {os.getpid()}")
        pg_session_engine = create_engine(
            get_db_connection_string(),
            pool_pre_ping=True,  # Verify connections before using them
            pool_recycle=3600,   # Recycle connections after 1 hour
            pool_size=5,         # Connections per worker
            max_overflow=10,     # Additional connections if needed
        )
        Session = sessionmaker(bind=pg_session_engine)
        logger.info(f"Database engine initialized for process {os.getpid()}")


@contextmanager
def pg_session():
    # Ensure engine is initialized (handles both preload and non-preload cases)
    init_db_engine()
    
    # prevent nested sessions to avoid difficult bugs
    if getattr(threadlocal, "inside_pg_session", False):
        raise Exception("Cannot open a pg session while already in a pg session")

    threadlocal.inside_pg_session = True
    session = Session()

    # roll back the transaction if any exception is raised
    try:
        yield session
        session.commit()
    except Exception as e:
        logger.error(f"Database error: {e}")
        session.rollback()
        raise
    finally:
        session.close()
        threadlocal.inside_pg_session = False


@contextmanager
def get_or_create_pg_session(session=None):
    """
    Return the PG session if it exists, or create a new one if it doesn't.
    """
    if session is not None:
        yield session
    else:
        with pg_session() as pg:
            yield pg


def init_db():
    """
    Run the schema.sql file on the database
    """
    logger.info("Initializing database...")

    with open("src/sql/migrations/schema.sql", "r") as f:
        schema_file = f.read()

    with pg_session() as pg:
        pg.execute(schema_file)

    logger.info("Done initializing database!")


def setup_db():
    """
    Prepare the database after the application starts

    This means running schema.sql if necessary (not in prod though) and running any
    migrations that have not been applied yet.
    
    This should be called BEFORE workers are forked (in master process with --preload)
    or in each worker if not using --preload.
    """
    global _setup_complete
    
    # Prevent running setup multiple times
    if _setup_complete:
        logger.info(f"Database setup already complete, skipping in process {os.getpid()}")
        return
    
    logger.info(f"Running database setup in process {os.getpid()}")
    
    if not db_exists():
        logger.info("Database was detected to be empty")
        # check the current environment; in production, raise an error
        env = os.environ["ENVIRONMENT"]
        if env == Env.PROD:
            logger.error(
                "Database was detected empty in production. "
                "Not attempting to create it to prevent any potential damage"
            )
            raise Exception("Database was detected empty in production")

        # initialize the database
        init_db()
    else:
        logger.info("Database already exists, no need to initialize it")

    migrations = list_migrations_to_apply()

    # we create the pg session here to ensure that if something fails, all the
    # migrations are rolled back
    with pg_session() as session:
        for m in migrations:
            apply_migration(session, m)
        load_base_data(session, "airliners")
        load_base_data(session, "wagons")

    _migrate_sqlite_nom_to_label()

    # Dispose the engine used during setup - workers will create their own
    global pg_session_engine
    if pg_session_engine is not None:
        logger.info(f"Disposing setup engine in process {os.getpid()}")
        pg_session_engine.dispose()
        pg_session_engine = None
    
    _setup_complete = True
    logger.info(f"Database setup complete in process {os.getpid()}")


def list_migrations_to_apply():
    """
    Check the list of migration files, and compare it with the list of migrations
    already applied on the database. Return the difference

    Migration files must follow this naming convention:
        1234_migration_name.sql
    where 1234 determines the order in which the migrations will be applied.
    """
    with pg_session() as pg:
        applied_migrations = pg.execute(sql.list_migrations()).fetchall()

    applied_migrations = [t[0] for t in applied_migrations]

    file_migrations = os.listdir("src/sql/migrations")
    # filter out non-migration files
    file_migrations = [f for f in file_migrations if re.match(r"\d{4}_.*\.sql", f)]
    # sort the list in order of migration number
    file_migrations.sort()

    migrations_to_apply = [f for f in file_migrations if f not in applied_migrations]
    logger.info(f"Found {len(migrations_to_apply)} migrations to apply")

    return migrations_to_apply


def apply_migration(session, name):
    """
    Apply the given migration on the database via the session passed in parameter
    """
    logger.info(f"Applying migration {name}")
    with open(f"src/sql/migrations/{name}") as f:
        migration_query = f.read()

    session.execute(migration_query)

    # keep track that the migration was applied
    query = "INSERT INTO meta.migrations (name) VALUES (:name)"
    session.execute(query, {"name": name})

    logger.info(f"Successfully applied migration {name}")


def db_exists():
    """
    Returns True if any table or schema already exists in the db
    """
    with pg_session() as pg:
        return pg.execute(sql.db_exists()).scalar()


def load_base_data(pg, table_name):
    """
    Load base data from CSV files into the database using COPY.
    Only loads data if the table is empty.
    
    Args:
        pg: PostgreSQL session
        table_name: Name of the table to load data into (also the CSV filename without extension)
    """
    # Check if table already has data
    result = pg.execute(f"SELECT COUNT(*) FROM {table_name}").scalar()
    
    if result > 0:
        logger.info(f"{table_name} table already contains {result} rows, skipping base data load")
        return
    
    logger.info(f"Loading base data for {table_name}...")
    
    csv_path = os.path.abspath(f"base_data/{table_name}.csv")
    
    if not os.path.exists(csv_path):
        logger.error(f"Base data file not found: {csv_path}")
        raise FileNotFoundError(f"Base data file not found: {csv_path}")
    
    # Use raw connection for COPY command
    raw_conn = pg.connection().connection
    with raw_conn.cursor() as cursor:
        with open(csv_path, 'r') as f:
            # Read the header to build an explicit column list so SERIAL/identity
            # columns (not present in the CSV) are handled transparently.
            columns = ', '.join(c.strip() for c in next(f).strip().split(','))
            cursor.copy_expert(
                f"COPY {table_name} ({columns}) FROM STDIN WITH (FORMAT CSV, NULL '')",
                f
            )

    logger.info(f"Base data loaded successfully for {table_name}!")


def _migrate_sqlite_nom_to_label():
    """Rename 'nom' -> 'label' in inline JSON compositions stored in the SQLite trip table.

    This is the SQLite counterpart of the DO block in migration 0020.
    It is idempotent: rows that already use 'label' (or have no 'nom') are skipped.
    """
    import json
    import sqlite3

    from src.consts import DbNames

    db_path = DbNames.MAIN_DB.value
    if not os.path.exists(db_path):
        logger.info("SQLite main.db not found, skipping nom→label migration")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT uid, material_type_advanced FROM trip "
            "WHERE material_type_advanced IS NOT NULL AND material_type_advanced LIKE '[%'"
        ).fetchall()

        updated = 0
        for row in rows:
            try:
                units = json.loads(row["material_type_advanced"])
                if not isinstance(units, list):
                    continue
                new_units, changed = [], False
                for u in units:
                    if "nom" in u and "label" not in u:
                        u = dict(u)
                        u["label"] = u.pop("nom")
                        changed = True
                    new_units.append(u)
                if changed:
                    conn.execute(
                        "UPDATE trip SET material_type_advanced = ? WHERE uid = ?",
                        (json.dumps(new_units), row["uid"]),
                    )
                    updated += 1
            except (json.JSONDecodeError, TypeError, KeyError):
                continue

        conn.commit()
        logger.info(f"SQLite: renamed 'nom' -> 'label' in {updated} trip composition(s)")
    finally:
        conn.close()