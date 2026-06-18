"""Duplicate a plan with all of its legs and shared costs.

Used to fork an itinerary into a new draft ("wire different possibilities") without
touching the original. The copy is a fresh plan (new uuid), unarchived, with every
plan_trip (geometry included) and plan_cost cloned; each leg's cost_id is remapped to
the corresponding new cost. Runs in one transaction.
"""

import logging
import uuid
from datetime import datetime

from src.pg import get_or_create_pg_session
from src.sql.plans import (
    get_plan_costs_query,
    get_plan_query,
    insert_plan_cost_query,
    insert_plan_query,
)

logger = logging.getLogger(__name__)

# Columns copied verbatim from the source legs (everything except the identity/
# plan_id/cost_id/timestamps, which are set explicitly below).
_PLAN_TRIP_COPY_COLS = (
    "user_id, sort_order, timing_mode, start_day, end_day, start_time, end_time, "
    "start_datetime, end_datetime, utc_start_datetime, utc_end_datetime, "
    "estimated_trip_duration, manual_trip_duration, origin_station, destination_station, "
    "trip_type, operator, line_name, material_type, material_type_advanced, reg, seat, "
    "notes, trip_length, countries, price, currency, purchase_date, booked, waypoints, "
    "visibility, carbon, power_type, co2_override, geom"
)


def duplicate_plan(plan_uuid, user_id, name=None, pg_session=None):
    """Clone the plan owned by user_id. Returns the new plan's uuid, or None if the
    plan does not exist or is not owned by user_id."""
    now = datetime.now()
    new_uuid = str(uuid.uuid4())

    with get_or_create_pg_session(pg_session) as pg:
        row = pg.execute(get_plan_query(), {"uuid": plan_uuid}).fetchone()
        if row is None or row._mapping["user_id"] != user_id:
            return None
        src = dict(row._mapping)

        new_plan_uid = pg.execute(
            insert_plan_query(),
            {
                "uuid": new_uuid,
                "user_id": user_id,
                "name": name or f"{src['name']} (copy)",
                "description": src["description"],
                "anchor_date": src["anchor_date"],
                "created": now,
                "last_modified": now,
            },
        ).fetchone()[0]

        # Clone shared costs first, building old->new id map for leg remapping.
        cost_map = {}
        for c in pg.execute(get_plan_costs_query(), {"plan_id": src["uid"]}).fetchall():
            cm = c._mapping
            new_cid = pg.execute(
                insert_plan_cost_query(),
                {
                    "plan_id": new_plan_uid,
                    "name": cm["name"],
                    "price": cm["price"],
                    "currency": cm["currency"],
                    "notes": cm["notes"],
                    "created": now,
                    "last_modified": now,
                },
            ).fetchone()[0]
            cost_map[cm["uid"]] = new_cid

        # cost ids are trusted integers from our own DB -> safe to inline in the CASE.
        if cost_map:
            whens = " ".join(f"WHEN {old} THEN {new}" for old, new in cost_map.items())
            cost_expr = f"CASE cost_id {whens} ELSE NULL END"
        else:
            cost_expr = "NULL"

        pg.execute(
            f"""
            INSERT INTO plan_trips ({_PLAN_TRIP_COPY_COLS}, plan_id, cost_id, created, last_modified)
            SELECT {_PLAN_TRIP_COPY_COLS}, :new_plan_uid, {cost_expr}, :created, :last_modified
            FROM plan_trips
            WHERE plan_id = :old_plan_uid
            """,
            {
                "new_plan_uid": new_plan_uid,
                "old_plan_uid": src["uid"],
                "created": now,
                "last_modified": now,
            },
        )

    logger.info(f"Duplicated plan {src['uid']} -> {new_plan_uid} ({new_uuid})")
    return new_uuid
