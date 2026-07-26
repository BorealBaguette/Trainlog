"""Move existing real trips into a plan (the reverse of validate_plan): copy each
trip into plan_trips with absolute (precise/onlyDate/unknown) timing, then delete the
original trip. Runs in one transaction so a failure leaves the trips untouched."""

import json
import logging
from datetime import datetime

from src.pg import get_or_create_pg_session
from src.plans.create_plan_trip import create_plan_trip
from src.plans.plan_trip import PlanTrip
from src.sql.trips import get_user_lines_query

logger = logging.getLogger(__name__)


def _timing_from_trip(r):
    """Map a real trip's stored datetimes onto a plan_trip timing dict. Imported trips
    are never relative (they already have absolute dates)."""
    sdt, edt = r["start_datetime"], r["end_datetime"]
    if sdt is None:  # project / unknown date
        mode, start_dt, end_dt, utc_s, utc_e = "unknown", None, None, None, None
    elif sdt.second == 1:  # date-only marker
        mode, start_dt, end_dt, utc_s, utc_e = "onlyDate", sdt, edt, None, None
    else:  # precise datetime
        mode, start_dt, end_dt = "precise", sdt, edt
        utc_s, utc_e = r["utc_start_datetime"], r["utc_end_datetime"]
    return {
        "timing_mode": mode,
        "start_day": None, "end_day": None, "start_time": None, "end_time": None,
        "start_datetime": start_dt, "end_datetime": end_dt,
        "utc_start_datetime": utc_s, "utc_end_datetime": utc_e,
        "manual_trip_duration": r["manual_trip_duration"],
    }


def import_trips_to_plan(plan, trip_ids, pg_session=None):
    """Move the given trips (only those owned by the plan's owner) into `plan`. Returns
    the list of trip ids actually moved."""
    now = datetime.now()
    moved = []
    with get_or_create_pg_session(pg_session) as pg:
        next_order = pg.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM plan_trips WHERE plan_id = :p",
            {"p": plan["uid"]},
        ).scalar()
        for tid in trip_ids:
            row = pg.execute(
                "SELECT * FROM trips WHERE trip_id = :id AND user_id = :uid",
                {"id": tid, "uid": plan["user_id"]},
            ).fetchone()
            if row is None:
                continue  # not found or not owned -> skip
            r = row._mapping
            pres = pg.execute(get_user_lines_query(), {"ids": [tid]}).fetchone()
            coords = (
                json.loads(pres._mapping["path"])
                if pres and pres._mapping["path"]
                else []
            )

            plan_trip = PlanTrip(
                plan_id=plan["uid"],
                user_id=plan["user_id"],
                origin_station=r["origin_station"],
                destination_station=r["destination_station"],
                trip_type=r["trip_type"],
                operator=r["operator"],
                line_name=r["line_name"],
                material_type=r["material_type"],
                material_type_advanced=r["material_type_advanced"],
                reg=r["reg"],
                seat=r["seat"],
                notes=r["notes"],
                trip_length=r["trip_length"],
                estimated_trip_duration=r["estimated_trip_duration"],
                countries=r["countries"],
                price=r["price"],
                currency=r["currency"],
                purchase_date=r["purchase_date"],
                # An imported real trip with a price was actually paid → booked.
                booked=r["price"] not in (None, ""),
                waypoints=r["waypoints"],
                visibility=r["visibility"],
                power_type=r["power_type"],
                co2_override=r["co2_override"],
                path=coords,
                timing=_timing_from_trip(r),
                sort_order=next_order,
                created=now,
                last_modified=now,
            )
            create_plan_trip(plan_trip, pg_session=pg)
            next_order += 1

            # Move = delete the original trip + its path and tag links.
            pg.execute("DELETE FROM tags_associations WHERE trip_id = :id", {"id": tid})
            pg.execute("DELETE FROM paths WHERE trip_id = :id", {"id": tid})
            pg.execute("DELETE FROM trips WHERE trip_id = :id", {"id": tid})
            moved.append(tid)

    logger.info(f"Moved {len(moved)} trips into plan {plan['uid']}")
    return moved
