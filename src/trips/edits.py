import datetime
import json

from flask import abort
from sqlalchemy import text

from src.carbon import calculate_carbon_footprint_for_trip
from src.consts import TripTypes
from src.pg import pg_session
from src.sql.trips import (
    attach_ticket_query,
    change_visibility_query,
    update_ticket_null_query,
    update_trip_type_query,
)
from src.utils import mainConn, managed_cursor, pathConn

from .trip import _strip_tags
from .utils import compare_trip

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


def attach_ticket_to_trips(username, ticket_id, trip_ids):
    try:
        last_modified = datetime.datetime.now()
        placeholders = ", ".join(["?"] * len(trip_ids))

        with managed_cursor(mainConn) as cursor:
            # Check ticket ownership
            cursor.execute(
                "SELECT 1 FROM tickets WHERE username = ? AND uid = ?",
                (username, ticket_id),
            )
            if cursor.fetchone() is None:
                abort(401)

            # Check all trip ownership
            cursor.execute(
                f"""
                SELECT COUNT(*) as c FROM trip 
                WHERE username = ? AND uid IN ({placeholders})
                """,
                [username] + trip_ids,
            )
            count = cursor.fetchone()["c"]
            if count != len(trip_ids):
                abort(401)

            cursor.execute(
                f"""
                UPDATE trip SET ticket_id = ?, last_modified = ?
                WHERE username = ? AND uid IN ({placeholders})
                """,
                [ticket_id, last_modified, username] + trip_ids,
            )

        with pg_session() as pg:
            for trip_id in trip_ids:
                pg.execute(
                    attach_ticket_query(),
                    {"trip_id": trip_id, "ticket_id": ticket_id, "last_modified": last_modified},
                )
        for trip_id in trip_ids:
            compare_trip(trip_id)

        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)


def change_trips_visibility(username, visibility, trip_ids):
    try:
        last_modified = datetime.datetime.now()
        placeholders = ", ".join(["?"] * len(trip_ids))

        if visibility not in ("public", "friends", "private"):
            abort(401)

        with managed_cursor(mainConn) as cursor:
            # Check all trip ownership
            cursor.execute(
                f"""
                SELECT COUNT(*) as c FROM trip 
                WHERE username = ? AND uid IN ({placeholders})
                """,
                [username] + trip_ids,
            )
            count = cursor.fetchone()["c"]
            if count != len(trip_ids):
                abort(401)

            cursor.execute(
                f"""
                UPDATE trip SET visibility = ?, last_modified = ?
                WHERE username = ? AND uid IN ({placeholders})
                """,
                [visibility, last_modified, username] + trip_ids,
            )

        with pg_session() as pg:
            for trip_id in trip_ids:
                pg.execute(
                    change_visibility_query(),
                    {"trip_id": trip_id, "visibility": visibility, "last_modified": last_modified},
                )
        for trip_id in trip_ids:
            compare_trip(trip_id)

        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)


def update_trip_type(trip_id, new_type: TripTypes):
    with pg_session() as pg:
        _update_trip_type_in_sqlite(trip_id, new_type)
        pg.execute(
            update_trip_type_query(), {"trip_id": trip_id, "trip_type": new_type.value}
        )


def _update_trip_type_in_sqlite(trip_id, new_type: TripTypes):
    with managed_cursor(mainConn) as cursor:
        cursor.execute(
            "UPDATE trip SET type = :newType WHERE uid = :tripId",
            {"newType": new_type.value, "tripId": trip_id},
        )
    mainConn.commit()


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

    try:
        placeholders = ", ".join(["?"] * len(trip_ids))

        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                f"SELECT COUNT(*) as c FROM trip WHERE username = ? AND uid IN ({placeholders})",
                [username] + trip_ids,
            )
            if cursor.fetchone()["c"] != len(trip_ids):
                abort(401)

            set_parts = ["last_modified = ?"]
            params = [last_modified]
            if safe_fields:
                for col, val in safe_fields.items():
                    if col == "notes" and notes_append:
                        set_parts.append(
                            "notes = CASE WHEN (notes IS NULL OR notes = '') THEN ? ELSE notes || char(10) || ? END"
                        )
                        params.extend([val, val])
                    else:
                        set_parts.append(f"{col} = ?")
                        params.append(val if val != "" else None)

            params.extend([username] + trip_ids)
            cursor.execute(
                f"UPDATE trip SET {', '.join(set_parts)} WHERE username = ? AND uid IN ({placeholders})",
                params,
            )

            if time_offset_minutes:
                modifier = f"{'+' if time_offset_minutes >= 0 else ''}{time_offset_minutes} minutes"
                cursor.execute(
                    f"""UPDATE trip SET
                        start_datetime = datetime(start_datetime, ?),
                        end_datetime = datetime(end_datetime, ?),
                        utc_start_datetime = CASE WHEN utc_start_datetime IS NOT NULL
                            THEN datetime(utc_start_datetime, ?) ELSE NULL END,
                        utc_end_datetime = CASE WHEN utc_end_datetime IS NOT NULL
                            THEN datetime(utc_end_datetime, ?) ELSE NULL END
                    WHERE username = ? AND uid IN ({placeholders})""",
                    [modifier, modifier, modifier, modifier, username] + trip_ids,
                )

        with pg_session() as pg:
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

        for trip_id in trip_ids:
            compare_trip(trip_id)

        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)


def bulk_change_type(username, trip_ids, new_type: TripTypes):
    last_modified = datetime.datetime.now()
    try:
        placeholders = ", ".join(["?"] * len(trip_ids))
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                f"SELECT COUNT(*) as c FROM trip WHERE username = ? AND uid IN ({placeholders})",
                [username] + trip_ids,
            )
            if cursor.fetchone()["c"] != len(trip_ids):
                abort(401)
            cursor.execute(
                f"UPDATE trip SET type = ?, last_modified = ? WHERE username = ? AND uid IN ({placeholders})",
                [new_type.value, last_modified, username] + trip_ids,
            )
        with pg_session() as pg:
            for trip_id in trip_ids:
                pg.execute(
                    text("UPDATE trips SET trip_type = :t, last_modified = :lm WHERE trip_id = :id"),
                    {"t": new_type.value, "lm": last_modified, "id": int(trip_id)},
                )
        for trip_id in trip_ids:
            compare_trip(trip_id)
        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)


def bulk_set_power_type(username, trip_ids, power_type: str):
    """Recalculate countries (elec/nonelec split) and carbon for each trip based on new power_type."""
    from py.utils import getCountriesFromPath
    last_modified = datetime.datetime.now()
    try:
        placeholders = ", ".join(["?"] * len(trip_ids))
        with managed_cursor(mainConn) as cursor:
            cursor.execute(
                f"SELECT uid, type, trip_length, countries, start_datetime FROM trip WHERE username = ? AND uid IN ({placeholders})",
                [username] + trip_ids,
            )
            trips = {row["uid"]: dict(row) for row in cursor.fetchall()}
        if len(trips) != len(trip_ids):
            abort(401)

        updates = []
        for trip_id in trip_ids:
            trip = trips[int(trip_id)]
            # Load path
            formatted = "({})".format(int(trip_id))
            with managed_cursor(pathConn) as cursor:
                row = cursor.execute(
                    f"SELECT path FROM paths WHERE trip_id = {int(trip_id)}"
                ).fetchone()
            if row is None:
                continue
            path = json.loads(row["path"])
            path_dicts = [{"lat": p[0], "lng": p[1]} for p in path]

            new_countries = getCountriesFromPath(path_dicts, trip["type"], powerType=power_type)
            trip_for_carbon = {
                "type": trip["type"],
                "trip_length": trip["trip_length"],
                "countries": new_countries,
                "start_datetime": trip["start_datetime"],
                "power_type": power_type,
            }
            carbon = calculate_carbon_footprint_for_trip(trip_for_carbon, path)
            updates.append((new_countries, carbon, int(trip_id)))

        with managed_cursor(mainConn) as cursor:
            for new_countries, carbon, trip_id in updates:
                cursor.execute(
                    "UPDATE trip SET countries = ?, last_modified = ? WHERE uid = ?",
                    [new_countries, last_modified, trip_id],
                )

        with pg_session() as pg:
            for new_countries, carbon, trip_id in updates:
                pg.execute(
                    text("UPDATE trips SET countries = :c, carbon = :carbon, last_modified = :lm WHERE trip_id = :id"),
                    {"c": new_countries, "carbon": carbon, "lm": last_modified, "id": trip_id},
                )

        for trip_id in trip_ids:
            compare_trip(trip_id)
        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)


def delete_ticket_from_db(username, ticket_id):
    try:
        trip_ids = []

        with managed_cursor(mainConn) as cursor:
            # Check ticket ownership
            cursor.execute(
                "SELECT 1 FROM tickets WHERE username = ? AND uid = ?",
                (username, ticket_id),
            )
            if cursor.fetchone() is None:
                abort(401)

            # Check trip ownership
            cursor.execute(
                "SELECT uid FROM trip WHERE username = ? AND ticket_id = ?",
                (username, ticket_id),
            )
            trip_ids = [row["uid"] for row in cursor.fetchall()]

            cursor.execute(
                "UPDATE trip SET ticket_id = NULL WHERE username = ? AND ticket_id = ?",
                (username, ticket_id),
            )
            cursor.execute(
                "DELETE FROM tickets WHERE username = ? AND uid = ?",
                (username, ticket_id),
            )

        with pg_session() as pg:
            for trip_id in trip_ids:
                pg.execute(update_ticket_null_query(), {"trip_id": trip_id})
        for trip_id in trip_ids:
            compare_trip(trip_id)

        mainConn.commit()
        return True, None
    except Exception as e:
        mainConn.rollback()
        return False, str(e)
