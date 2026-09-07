"""Convert a plan's plan_trips into real trips, anchored at a chosen Day-1 date.
Reuses create_trip unchanged.

Legs can be logged all at once or a few at a time (`plan_trip_uids`). A logged leg
keeps its plan row and records the trip it produced in `validated_trip_id`, so it is
skipped by every later batch; the plan is archived only once nothing is left to log.
Successive batches join the same tag (plans.validated_tag_uuid) and reuse the ticket a
shared cost was already exported as (plan_costs.ticket_id), so nothing is duplicated.
"""

import logging
import uuid as uuid_lib
from datetime import datetime, time as dtime, timedelta

from src.paths import geom_geojson_to_coords
from src.pg import get_or_create_pg_session
from src.sql.plans import archive_plan_query, get_plan_costs_query, get_plan_trips_query
from src.trips.create_trip import create_trip
from src.trips.trip import Trip
from src.utils import get_username, getUtcDatetime

logger = logging.getLogger(__name__)


def validate_plan(plan, start_date, plan_trip_uids=None, pg_session=None):
    """`plan` is the plans row mapping; `start_date` a datetime.date anchoring Day 1.
    `plan_trip_uids` restricts the batch to those legs (None = every leg not already
    logged). Returns the uuid of the tag grouping the plan's logged trips, or None when
    the batch created nothing. Runs in one (optionally shared) session."""
    username = get_username(plan["user_id"])
    now = datetime.now()
    created_ids = []
    cost_to_trips = {}  # plan_costs.uid -> [created trip_id, ...]
    tag_uuid = plan.get("validated_tag_uuid")
    wanted = set(plan_trip_uids) if plan_trip_uids is not None else None

    with get_or_create_pg_session(pg_session) as pg:
        rows = pg.execute(get_plan_trips_query(), {"plan_id": plan["uid"]}).fetchall()
        for r in rows:
            pt = r._mapping
            # Already logged in an earlier batch, or not part of this one.
            if pt["validated_trip_id"] is not None:
                continue
            if wanted is not None and pt["uid"] not in wanted:
                continue
            coords = geom_geojson_to_coords(pt["geojson"])  # [[lat,lng],...]
            path = [{"lat": c[0], "lng": c[1]} for c in coords]

            if pt["timing_mode"] == "relative" and pt["start_day"] is not None:
                if pt["start_time"] is not None and pt["end_time"] is not None:
                    # timed leg -> precise dated trip, anchored at the chosen start date
                    start_dt = datetime.combine(
                        start_date + timedelta(days=pt["start_day"] - 1), pt["start_time"]
                    )
                    end_dt = datetime.combine(
                        start_date + timedelta(days=(pt["end_day"] or pt["start_day"]) - 1),
                        pt["end_time"],
                    )
                    if path:
                        utc_start = getUtcDatetime(
                            dateTime=start_dt, lat=path[0]["lat"], lng=path[0]["lng"]
                        )
                        utc_end = getUtcDatetime(
                            dateTime=end_dt, lat=path[-1]["lat"], lng=path[-1]["lng"]
                        )
                    else:
                        utc_start = utc_end = None
                else:
                    # untimed leg -> date-only real trip (00:00:01 marker), no UTC
                    start_dt = datetime.combine(
                        start_date + timedelta(days=pt["start_day"] - 1), dtime(0, 0, 1)
                    )
                    end_dt = datetime.combine(
                        start_date + timedelta(days=(pt["end_day"] or pt["start_day"]) - 1),
                        dtime(0, 0, 1),
                    )
                    utc_start = utc_end = None
            else:
                # precise / onlyDate / unknown -> use the stored materialised values.
                start_dt = pt["start_datetime"]
                end_dt = pt["end_datetime"]
                utc_start = pt["utc_start_datetime"]
                utc_end = pt["utc_end_datetime"]

            trip = Trip(
                username=username,
                user_id=pt["user_id"],
                origin_station=pt["origin_station"],
                destination_station=pt["destination_station"],
                start_datetime=start_dt,
                end_datetime=end_dt,
                utc_start_datetime=utc_start,
                utc_end_datetime=utc_end,
                trip_length=pt["trip_length"],
                estimated_trip_duration=pt["estimated_trip_duration"],
                manual_trip_duration=pt["manual_trip_duration"],
                operator=pt["operator"],
                countries=pt["countries"],
                line_name=pt["line_name"],
                created=now,
                last_modified=now,
                type=pt["trip_type"],
                material_type=pt["material_type"],
                material_type_advanced=pt["material_type_advanced"],
                seat=pt["seat"],
                reg=pt["reg"],
                waypoints=pt["waypoints"],
                notes=pt["notes"],
                price=pt["price"],
                currency=pt["currency"],
                purchasing_date=pt["purchase_date"],
                ticket_id=None,
                # date-less -> a project (future/unknown); dated -> a real trip
                is_project=start_dt is None,
                path=path,
                visibility=pt["visibility"],
                power_type=pt["power_type"],
                co2_override=pt["co2_override"],
            )
            trip_id = create_trip(trip, pg_session=pg)
            created_ids.append(trip_id)
            # Pin the leg to the trip it produced: it is now logged, and no later
            # batch (nor a full "log everything") will duplicate it.
            pg.execute(
                "UPDATE plan_trips SET validated_trip_id = :trip_id, last_modified = :now"
                " WHERE uid = :uid AND plan_id = :plan_id",
                {"trip_id": trip_id, "now": now, "uid": pt["uid"], "plan_id": plan["uid"]},
            )
            if pt["cost_id"] is not None:
                cost_to_trips.setdefault(pt["cost_id"], []).append(trip_id)

        # Export each shared cost as a real ticket linked to exactly its trips, so the
        # main-level price_per_trip / price_per_km work natively (nothing lost). A cost
        # whose legs are logged over several batches keeps its first ticket (its price
        # is the whole cost, not a per-batch share) and the later trips join it.
        if cost_to_trips:
            costs = pg.execute(
                get_plan_costs_query(), {"plan_id": plan["uid"]}
            ).fetchall()
            for c in costs:
                cm = c._mapping
                trip_ids = cost_to_trips.get(cm["uid"])
                if not trip_ids:
                    continue
                ticket_uid = cm["ticket_id"]
                if ticket_uid is None:
                    ticket_uid = pg.execute(
                        "INSERT INTO tickets (name, username, price, currency, purchasing_date, notes)"
                        " VALUES (:name, :username, :price, :currency, :purchasing_date, :notes)"
                        " RETURNING uid",
                        {
                            "name": cm["name"],
                            "username": username,
                            "price": cm["price"],
                            "currency": cm["currency"] or "EUR",
                            "purchasing_date": start_date,
                            "notes": cm["notes"],
                        },
                    ).fetchone()[0]
                    pg.execute(
                        "UPDATE plan_costs SET ticket_id = :ticket_id, last_modified = :now"
                        " WHERE uid = :uid AND plan_id = :plan_id",
                        {"ticket_id": ticket_uid, "now": now, "uid": cm["uid"],
                         "plan_id": plan["uid"]},
                    )
                pg.execute(
                    "UPDATE trips SET ticket_id = :tid WHERE trip_id = ANY(:ids)",
                    {"tid": ticket_uid, "ids": trip_ids},
                )

        # Group the created trips under a tag named after the plan, so the validated
        # voyage stays together (and gets a shareable link). Batches after the first
        # join the tag the plan already has. Returned for redirect.
        if created_ids:
            tag_uid = None
            if tag_uuid:
                row = pg.execute(
                    "SELECT uid FROM tags WHERE uuid = :uuid AND username = :username",
                    {"uuid": tag_uuid, "username": username},
                ).fetchone()
                tag_uid = row[0] if row else None
            if tag_uid is None:
                # No tag yet (first batch), or the user deleted it — make a new one.
                tag_uuid = str(uuid_lib.uuid4())
                tag_uid = pg.execute(
                    "INSERT INTO tags (username, name, colour, uuid, type)"
                    " VALUES (:username, :name, :colour, :uuid, :type) RETURNING uid",
                    {
                        "username": username,
                        "name": plan["name"],
                        "colour": "#4b78d6",
                        "uuid": tag_uuid,
                        "type": "voyage",
                    },
                ).fetchone()[0]
                pg.execute(
                    "UPDATE plans SET validated_tag_uuid = :tag_uuid WHERE uid = :uid",
                    {"tag_uuid": tag_uuid, "uid": plan["uid"]},
                )
            for trip_id in created_ids:
                pg.execute(
                    "INSERT INTO tags_associations (tag_id, trip_id)"
                    " VALUES (:tag_id, :trip_id) ON CONFLICT (tag_id, trip_id) DO NOTHING",
                    {"tag_id": tag_uid, "trip_id": trip_id},
                )

        # Archive only once the whole plan has been logged — a partially logged plan
        # stays active so the legs still to come can be worked on.
        remaining = pg.execute(
            "SELECT COUNT(*) FROM plan_trips WHERE plan_id = :plan_id"
            " AND validated_trip_id IS NULL",
            {"plan_id": plan["uid"]},
        ).fetchone()[0]
        if remaining == 0:
            pg.execute(
                archive_plan_query(),
                {
                    "uid": plan["uid"],
                    "user_id": plan["user_id"],
                    "archived": True,
                    "last_modified": now,
                },
            )

    logger.info(
        f"Validated plan {plan['uid']} -> {len(created_ids)} trips "
        f"({remaining} leg(s) still unlogged)"
    )
    return tag_uuid
