import datetime

from flask import abort
from sqlalchemy import text

from src.carbon import calculate_carbon_footprint_for_trip
from src.consts import TripTypes
from src.paths import fetch_path
from src.pg import pg_session
from src.sql.trips import (
    attach_ticket_query,
    change_visibility_query,
    update_ticket_null_query,
    update_trip_type_query,
)
from src.utils import get_user_id

from .trip import _strip_tags

STRIPPED_BULK_EDIT_FIELDS = frozenset([
    "operator", "line_name", "reg", "seat", "notes",
    "origin_station", "destination_station", "material_type",
])

ALLOWED_BULK_EDIT_FIELDS = frozenset(
    [
        "operator",
        "line_name",
        "reg",
        "seat",
        "notes",
        "visibility",
        "origin_station",
        "destination_station",
        "material_type",
        "material_type_advanced",
        "departure_delay",
        "arrival_delay",
    ]
)


def _own_all_trips(pg, user_id, trip_ids):
    """True if all trip_ids belong to user_id (PostgreSQL)."""
    count = pg.execute(
        "SELECT COUNT(*) FROM trips WHERE user_id = :user_id AND trip_id = ANY(:ids)",
        {"user_id": user_id, "ids": [int(t) for t in trip_ids]},
    ).scalar()
    return count == len(trip_ids)


def attach_ticket_to_trips(username, ticket_id, trip_ids):
    try:
        last_modified = datetime.datetime.now()
        user_id = get_user_id(username)
        with pg_session() as pg:
            # Ticket ownership
            if (
                pg.execute(
                    "SELECT 1 FROM tickets WHERE username = :username AND uid = :ticket_id",
                    {"username": username, "ticket_id": ticket_id},
                ).fetchone()
                is None
            ):
                abort(401)
            # Trip ownership
            if not _own_all_trips(pg, user_id, trip_ids):
                abort(401)
            for trip_id in trip_ids:
                pg.execute(
                    attach_ticket_query(),
                    {
                        "trip_id": trip_id,
                        "ticket_id": ticket_id,
                        "last_modified": last_modified,
                    },
                )
        return True, None
    except Exception as e:
        return False, str(e)


def change_trips_visibility(username, visibility, trip_ids):
    try:
        last_modified = datetime.datetime.now()
        if visibility not in ("public", "friends", "private"):
            abort(401)
        user_id = get_user_id(username)
        with pg_session() as pg:
            if not _own_all_trips(pg, user_id, trip_ids):
                abort(401)
            for trip_id in trip_ids:
                pg.execute(
                    change_visibility_query(),
                    {
                        "trip_id": trip_id,
                        "visibility": visibility,
                        "last_modified": last_modified,
                    },
                )
        return True, None
    except Exception as e:
        return False, str(e)


def update_trip_type(trip_id, new_type: TripTypes):
    with pg_session() as pg:
        pg.execute(
            update_trip_type_query(), {"trip_id": trip_id, "trip_type": new_type.value}
        )


def bulk_edit_trips(
    username, trip_ids, fields: dict, notes_append: bool = False, time_offset_minutes: int = 0
):
    safe_fields = {
        k: (_strip_tags(v) if k in STRIPPED_BULK_EDIT_FIELDS else v)
        for k, v in fields.items()
        if k in ALLOWED_BULK_EDIT_FIELDS
    }
    if not safe_fields and not time_offset_minutes:
        return False, "No valid fields to update"

    last_modified = datetime.datetime.now()
    user_id = get_user_id(username)

    try:
        with pg_session() as pg:
            if not _own_all_trips(pg, user_id, trip_ids):
                abort(401)

            for trip_id in trip_ids:
                pg_set_parts = ["last_modified = :last_modified"]
                pg_params = {"trip_id": int(trip_id), "last_modified": last_modified}
                if safe_fields:
                    for col, val in safe_fields.items():
                        if col == "notes" and notes_append:
                            pg_set_parts.append(
                                "notes = CASE WHEN (notes IS NULL OR notes = '') THEN :notes ELSE notes || chr(10) || :notes END"
                            )
                            pg_params["notes"] = val
                        else:
                            pg_set_parts.append(f"{col} = :{col}")
                            pg_params[col] = val if val != "" else None
                pg.execute(
                    text(f"UPDATE trips SET {', '.join(pg_set_parts)} WHERE trip_id = :trip_id"),
                    pg_params,
                )

                if time_offset_minutes:
                    offset_secs = time_offset_minutes * 60
                    pg.execute(
                        text("""UPDATE trips SET
                            start_datetime = start_datetime + :offset * interval '1 second',
                            end_datetime = end_datetime + :offset * interval '1 second',
                            utc_start_datetime = CASE WHEN utc_start_datetime IS NOT NULL
                                THEN utc_start_datetime + :offset * interval '1 second' ELSE NULL END,
                            utc_end_datetime = CASE WHEN utc_end_datetime IS NOT NULL
                                THEN utc_end_datetime + :offset * interval '1 second' ELSE NULL END
                        WHERE trip_id = :trip_id"""),
                        {"offset": offset_secs, "trip_id": int(trip_id)},
                    )

        return True, None
    except Exception as e:
        return False, str(e)


def bulk_change_type(username, trip_ids, new_type: TripTypes):
    last_modified = datetime.datetime.now()
    user_id = get_user_id(username)
    try:
        with pg_session() as pg:
            if not _own_all_trips(pg, user_id, trip_ids):
                abort(401)
            for trip_id in trip_ids:
                pg.execute(
                    text("UPDATE trips SET trip_type = :t, last_modified = :lm WHERE trip_id = :id"),
                    {"t": new_type.value, "lm": last_modified, "id": int(trip_id)},
                )
        return True, None
    except Exception as e:
        return False, str(e)


def bulk_set_power_type(username, trip_ids, power_type: str):
    """Recalculate countries (elec/nonelec split) and carbon for each trip based on new power_type."""
    from py.utils import getCountriesFromPath

    last_modified = datetime.datetime.now()
    user_id = get_user_id(username)
    try:
        with pg_session() as pg:
            trips = {
                row["trip_id"]: dict(row._mapping)
                for row in pg.execute(
                    "SELECT trip_id, trip_type AS type, trip_length, countries, start_datetime"
                    " FROM trips WHERE user_id = :user_id AND trip_id = ANY(:ids)",
                    {"user_id": user_id, "ids": [int(t) for t in trip_ids]},
                ).fetchall()
            }
            if len(trips) != len(trip_ids):
                abort(401)

            for trip_id in trip_ids:
                trip = trips[int(trip_id)]
                path = fetch_path(pg, int(trip_id))
                if not path:
                    continue
                path_dicts = [{"lat": p[0], "lng": p[1]} for p in path]

                new_countries = getCountriesFromPath(
                    path_dicts, trip["type"], powerType=power_type
                )
                trip_for_carbon = {
                    "type": trip["type"],
                    "trip_length": trip["trip_length"],
                    "countries": new_countries,
                    "start_datetime": trip["start_datetime"],
                    "power_type": power_type,
                }
                carbon = calculate_carbon_footprint_for_trip(trip_for_carbon, path)
                pg.execute(
                    text("UPDATE trips SET countries = :c, carbon = :carbon, last_modified = :lm WHERE trip_id = :id"),
                    {"c": new_countries, "carbon": carbon, "lm": last_modified, "id": int(trip_id)},
                )
        return True, None
    except Exception as e:
        return False, str(e)


def delete_ticket_from_db(username, ticket_id):
    try:
        with pg_session() as pg:
            # Ticket ownership
            if (
                pg.execute(
                    "SELECT 1 FROM tickets WHERE username = :username AND uid = :ticket_id",
                    {"username": username, "ticket_id": ticket_id},
                ).fetchone()
                is None
            ):
                abort(401)

            # Detach the ticket from all of the user's trips
            trip_ids = [
                row[0]
                for row in pg.execute(
                    "SELECT trip_id FROM trips WHERE user_id = :user_id AND ticket_id = :ticket_id",
                    {"user_id": get_user_id(username), "ticket_id": ticket_id},
                ).fetchall()
            ]
            for trip_id in trip_ids:
                pg.execute(update_ticket_null_query(), {"trip_id": trip_id})

            pg.execute(
                "DELETE FROM tickets WHERE username = :username AND uid = :ticket_id",
                {"username": username, "ticket_id": ticket_id},
            )
        return True, None
    except Exception as e:
        return False, str(e)
