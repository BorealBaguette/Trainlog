"""Convert a plan's plan_trips into real trips, anchored at a chosen Day-1 date,
then archive the plan (keep it for reference). Reuses create_trip unchanged."""

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


def validate_plan(plan, start_date, pg_session=None):
    """`plan` is the plans row mapping; `start_date` a datetime.date anchoring Day 1.
    Returns the list of created trip ids. Runs in one (optionally shared) session."""
    username = get_username(plan["user_id"])
    now = datetime.now()
    created_ids = []
    cost_to_trips = {}  # plan_costs.uid -> [created trip_id, ...]
    tag_uuid = None

    with get_or_create_pg_session(pg_session) as pg:
        rows = pg.execute(get_plan_trips_query(), {"plan_id": plan["uid"]}).fetchall()
        for r in rows:
            pt = r._mapping
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
            if pt["cost_id"] is not None:
                cost_to_trips.setdefault(pt["cost_id"], []).append(trip_id)

        # Export each shared cost as a real ticket linked to exactly its trips, so the
        # main-level price_per_trip / price_per_km work natively (nothing lost).
        if cost_to_trips:
            costs = pg.execute(
                get_plan_costs_query(), {"plan_id": plan["uid"]}
            ).fetchall()
            for c in costs:
                cm = c._mapping
                trip_ids = cost_to_trips.get(cm["uid"])
                if not trip_ids:
                    continue
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
                    "UPDATE trips SET ticket_id = :tid WHERE trip_id = ANY(:ids)",
                    {"tid": ticket_uid, "ids": trip_ids},
                )

        # Group the created trips under a tag named after the plan, so the validated
        # voyage stays together (and gets a shareable link). Returned for redirect.
        if created_ids:
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
            for trip_id in created_ids:
                pg.execute(
                    "INSERT INTO tags_associations (tag_id, trip_id)"
                    " VALUES (:tag_id, :trip_id) ON CONFLICT (tag_id, trip_id) DO NOTHING",
                    {"tag_id": tag_uid, "trip_id": trip_id},
                )

        pg.execute(
            archive_plan_query(),
            {
                "uid": plan["uid"],
                "user_id": plan["user_id"],
                "archived": True,
                "last_modified": now,
            },
        )

    logger.info(f"Validated plan {plan['uid']} -> {len(created_ids)} trips")
    return tag_uuid
