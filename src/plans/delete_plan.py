import logging

from src.pg import get_or_create_pg_session
from src.sql.plans import delete_plan_query, delete_plan_trip_query

logger = logging.getLogger(__name__)


def delete_plan(uid, user_id, pg_session=None):
    """Delete a plan; ON DELETE CASCADE removes its plan_trips (and inline geom)."""
    with get_or_create_pg_session(pg_session) as pg:
        pg.execute(delete_plan_query(), {"uid": uid, "user_id": user_id})
    logger.info(f"Deleted plan {uid}")


def delete_plan_trip(uid, user_id, pg_session=None):
    with get_or_create_pg_session(pg_session) as pg:
        pg.execute(delete_plan_trip_query(), {"uid": uid, "user_id": user_id})
    logger.info(f"Deleted plan_trip {uid}")
