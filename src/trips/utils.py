from src.pg import pg_session
from src.sql.trips import get_current_trip_query
from src.utils import get_user_id

from .trip import Trip


def get_current_trip_id() -> Trip | None:
    with pg_session() as pg:
        trip = pg.execute(
            get_current_trip_query(), {"user_id": get_user_id()}
        ).fetchone()
        return trip["trip_id"] if trip is not None else None
