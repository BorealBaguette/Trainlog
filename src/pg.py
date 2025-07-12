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


@contextmanager
def pg_session():
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
    """
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


# setup to easily create database sessions
pg_session_engine = create_engine(get_db_connection_string())
Session = sessionmaker(bind=pg_session_engine)
