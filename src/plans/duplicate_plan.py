"""Duplicate a plan with all of its legs and shared costs.

Used to fork an itinerary into a new draft ("wire different possibilities") without
touching the original. The copy is a fresh plan (new uuid), unarchived, with every
plan_trip (geometry included) and plan_cost cloned; each leg's cost_id is remapped to
the corresponding new cost. Runs in one transaction.

The copy always lands in `user_id`'s account. With `require_same_user=True` (the
default) that is an in-account fork: the source plan must already belong to `user_id`.
With `require_same_user=False` the source plan may have been created by a different
user (saving a shared plan into your own plans); the caller is then responsible for
checking that `user_id` is allowed to read it.
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
# user_id/plan_id/cost_id/timestamps, which are set explicitly below — user_id is
# rewritten so a copy of another user's plan belongs to the user copying it).
_PLAN_TRIP_COPY_COLS = (
    "sort_order, timing_mode, start_day, end_day, start_time, end_time, weekdays, "
    "start_datetime, end_datetime, utc_start_datetime, utc_end_datetime, "
    "estimated_trip_duration, manual_trip_duration, origin_station, destination_station, "
    "trip_type, operator, line_name, material_type, material_type_advanced, reg, seat, "
    "notes, trip_length, countries, price, currency, purchase_date, booked, waypoints, "
    "visibility, carbon, power_type, co2_override, geom"
)


def duplicate_plan(plan_uuid, user_id, name=None, pg_session=None, require_same_user=True,
                   visibilities=None):
    """Clone a plan into user_id's account. Returns the new plan's uuid, or None if the
    plan does not exist (or, when require_same_user, was created by someone else).
    `visibilities` (None = all) keeps only the legs with those visibilities: a copy of
    someone else's plan holds what its copier could see, nothing more."""
    now = datetime.now()
    new_uuid = str(uuid.uuid4())

    with get_or_create_pg_session(pg_session) as pg:
        row = pg.execute(get_plan_query(), {"uuid": plan_uuid}).fetchone()
        if row is None or (require_same_user and row._mapping["user_id"] != user_id):
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

        # Rail-pass travel days: the setting and the marked days.
        pg.execute(
            "UPDATE plans SET pass_days_enabled = :on WHERE uid = :uid",
            {"on": bool(src.get("pass_days_enabled")), "uid": new_plan_uid},
        )
        pg.execute(
            "INSERT INTO plan_pass_days (plan_id, day_key, status)"
            " SELECT :new, day_key, status FROM plan_pass_days WHERE plan_id = :old",
            {"new": new_plan_uid, "old": src["uid"]},
        )

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

        # Trips linked into the plan stay linked in a copy in the same account (the
        # possibilities built around the same booked flight). Another account can't
        # link someone else's trip: it gets them as plain legs, as they are now.
        keep_links = src["user_id"] == user_id
        pg.execute(
            f"""
            INSERT INTO plan_trips ({_PLAN_TRIP_COPY_COLS}, user_id, plan_id, cost_id, linked_trip_id, created, last_modified)
            SELECT {_PLAN_TRIP_COPY_COLS}, :new_user_id, :new_plan_uid, {cost_expr},
                   linked_trip_id, :created, :last_modified
            FROM plan_trips
            WHERE plan_id = :old_plan_uid AND (linked_trip_id IS NULL OR :keep_links)
              AND (CAST(:visibilities AS text[]) IS NULL
                   OR COALESCE(visibility, 'private') = ANY(CAST(:visibilities AS text[])))
            """,
            {
                "new_user_id": user_id,
                "new_plan_uid": new_plan_uid,
                "old_plan_uid": src["uid"],
                "keep_links": keep_links,
                "visibilities": list(visibilities) if visibilities is not None else None,
                "created": now,
                "last_modified": now,
            },
        )
        if not keep_links:
            from src.plans.import_trips import copy_trip_into_plan

            for leg in pg.execute(
                "SELECT t.*, pt.sort_order AS leg_order FROM plan_trips pt"
                " JOIN trips t ON t.trip_id = pt.linked_trip_id"
                " WHERE pt.plan_id = :old_plan_uid AND (CAST(:visibilities AS text[]) IS NULL"
                " OR COALESCE(t.visibility, 'private') = ANY(CAST(:visibilities AS text[])))",
                {"old_plan_uid": src["uid"],
                 "visibilities": list(visibilities) if visibilities is not None else None},
            ).fetchall():
                copy_trip_into_plan(pg, leg._mapping, new_plan_uid, user_id,
                                    leg._mapping["leg_order"], now)

    logger.info(f"Duplicated plan {src['uid']} -> {new_plan_uid} ({new_uuid})")
    return new_uuid
