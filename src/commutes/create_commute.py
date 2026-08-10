import logging

from sqlalchemy.exc import IntegrityError

from src.pg import pg_session
from src.sql.commutes import (
    get_commute_query,
    get_first_occurrence_id_query,
    get_max_sequence_query,
    insert_commute_occurrence_query,
    insert_commute_query,
    insert_commute_route_trip_query,
    touch_commute_query,
)

logger = logging.getLogger(__name__)


def create_commute(user_id: int, name: str, trip_ids: list[int]) -> int:
    """
    Create a new commute from one or more existing trips owned by user_id,
    ordered by start_datetime into a single leg sequence. Works the same
    whether trip_ids covers only one direction of a round trip, or the whole
    thing logged retroactively in one go -- ordering by start_datetime is
    enough to get the travel order right either way.
    Returns the new commute_routes.uid.
    """
    if not trip_ids:
        raise ValueError("A commute needs at least one trip")

    ordered_trip_ids = _owned_trip_ids_ordered(user_id, trip_ids)

    try:
        with pg_session() as pg:
            commute_route_id = pg.execute(
                insert_commute_query(), {"user_id": user_id, "name": name}
            ).fetchone()[0]

            occurrence_id = pg.execute(
                insert_commute_occurrence_query(),
                {"commute_route_id": commute_route_id},
            ).fetchone()[0]

            for sequence, trip_id in enumerate(ordered_trip_ids, start=1):
                pg.execute(
                    insert_commute_route_trip_query(),
                    {
                        "occurrence_id": occurrence_id,
                        "trip_id": trip_id,
                        "sequence": sequence,
                    },
                )
    except IntegrityError:
        # pg_session() already rolled back the whole transaction (including
        # the commute_routes insert), so there's nothing left to clean up.
        raise ValueError("One or more of these trips is already part of a commute")

    logger.info(
        f"Created commute {commute_route_id} for user {user_id} with"
        f" {len(ordered_trip_ids)} leg(s)"
    )
    return commute_route_id


def append_legs(user_id: int, commute_route_id: int, trip_ids: list[int]) -> int:
    """
    Append one or more existing trips owned by user_id to the end of an
    existing commute's leg sequence -- e.g. adding the return leg(s) of a
    round trip after the outbound was already saved, or adding a further
    stop to an open chain. Trips are ordered by start_datetime among
    themselves before appending. Returns the number of legs appended.
    """
    if not trip_ids:
        raise ValueError("No trips given to append")

    with pg_session() as pg:
        commute = pg.execute(
            get_commute_query(), {"uid": commute_route_id, "user_id": user_id}
        ).fetchone()
    if commute is None:
        raise PermissionError("Commute does not belong to this user")

    ordered_trip_ids = _owned_trip_ids_ordered(user_id, trip_ids)

    try:
        with pg_session() as pg:
            max_sequence = pg.execute(
                get_max_sequence_query(), {"commute_route_id": commute_route_id}
            ).fetchone()[0]
            occurrence_id = pg.execute(
                get_first_occurrence_id_query(),
                {"commute_route_id": commute_route_id},
            ).fetchone()[0]

            for offset, trip_id in enumerate(ordered_trip_ids, start=1):
                pg.execute(
                    insert_commute_route_trip_query(),
                    {
                        "occurrence_id": occurrence_id,
                        "trip_id": trip_id,
                        "sequence": max_sequence + offset,
                    },
                )

            pg.execute(touch_commute_query(), {"uid": commute_route_id})
    except IntegrityError:
        raise ValueError("One or more of these trips is already part of a commute")

    logger.info(
        f"Appended {len(ordered_trip_ids)} leg(s) to commute {commute_route_id}"
    )
    return len(ordered_trip_ids)


def _owned_trip_ids_ordered(user_id: int, trip_ids: list[int]) -> list[int]:
    if len(set(trip_ids)) != len(trip_ids):
        raise ValueError("Duplicate trip ids")

    with pg_session() as pg:
        rows = pg.execute(
            "SELECT trip_id, start_datetime FROM trips"
            " WHERE trip_id = ANY(:ids) AND user_id = :user_id",
            {"ids": trip_ids, "user_id": user_id},
        ).fetchall()

    if len(rows) != len(trip_ids):
        raise ValueError("One or more trip ids are invalid or don't belong to this user")

    for row in rows:
        start_datetime = row["start_datetime"]
        # NULL means an unknown-date/project trip (src/utils.py's
        # processDates() stores those with start_datetime=None); a literal
        # ":01" seconds marker means a date-only trip. Either way there's no
        # real departure/arrival time to build a commute's leg schedule
        # from, so a template built from it can never be logged against.
        if start_datetime is None or start_datetime.second == 1:
            raise ValueError(
                f"Trip {row['trip_id']} has no precise date and time, so it"
                " can't be used in a commute"
            )

    ordered = sorted(rows, key=lambda r: r["start_datetime"])
    return [row["trip_id"] for row in ordered]
