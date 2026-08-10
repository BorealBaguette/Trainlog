import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from src.pg import pg_session
from src.sql.commutes import (
    get_commute_template_query,
    insert_commute_occurrence_query,
    insert_commute_route_trip_query,
)
from src.sql.trips import delete_trip_query, get_user_lines_query
from src.trips.duplicate_trip import duplicate_trip
from src.trips.trip import Trip
from src.trips.update_trip import update_trip
from src.utils import get_username, processDates

logger = logging.getLogger(__name__)


def get_commute_template(commute_route_id: int) -> list[dict]:
    """
    Legs of a commute's template (its first-ever occurrence), in travel
    order: trip_id, sequence, origin/destination station, and the
    scheduled start/end time-of-day. Day-rollover isn't resolved here --
    it depends on the actual times used for a given occurrence (which may
    override the template), so it's computed in log_commute_occurrence()
    against the effective times, not this frozen template.
    """
    with pg_session() as pg:
        rows = pg.execute(
            get_commute_template_query(), {"commute_route_id": commute_route_id}
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def log_commute_occurrence(
    commute_route_id: int, date: str, leg_inputs: list[dict]
) -> list[int]:
    """
    Clone a chosen subset (usually all) of a commute's template legs,
    reschedule each onto `date` ("YYYY-MM-DD") using the template's
    scheduled time-of-day for that leg by default (overridable per leg),
    apply any per-leg delay, and group the new trips into a new
    commute_occurrences row.

    leg_inputs: one entry per leg to include this time, in travel order --
    {"sequence": int, "startTime": "HH:MM"|None, "endTime": "HH:MM"|None,
    "departureDelay": minutes|None, "arrivalDelay": minutes|None}.
    startTime/endTime override the template's scheduled time for this
    occurrence only (e.g. missed the usual connection and caught the next,
    differently-timed service instead -- that's a different base time, not
    a delay against a train never actually taken); omitted or None falls
    back to the template's scheduled time. A leg skipped here simply isn't
    logged for this occurrence; it's still part of the template for next
    time.

    Validates that the resulting legs stay in chronological order (a leg
    can't depart before the previous one's actual, delay-adjusted arrival;
    a 0-minute connection is fine, a negative one isn't) before creating
    anything -- mirrors the client-side check in commute_list.html, since
    the API has no other guard against nonsensical input.

    Returns the new trip ids, in sequence order.
    """
    template = get_commute_template(commute_route_id)
    if not template:
        raise ValueError(f"Commute {commute_route_id} has no legs to log")
    if not leg_inputs:
        raise ValueError("At least one leg must be included")

    template_by_sequence = {leg["sequence"]: leg for leg in template}
    anchor_date = datetime.strptime(date, "%Y-%m-%d").date()

    resolved = []
    for leg_input in leg_inputs:
        sequence = leg_input.get("sequence")
        leg = template_by_sequence.get(sequence)
        if leg is None:
            raise ValueError(f"No such leg (sequence {sequence}) on this commute")

        start_time_str = leg_input.get("startTime") or leg["scheduled_start"].strftime(
            "%H:%M"
        )
        end_time_str = leg_input.get("endTime") or leg["scheduled_end"].strftime(
            "%H:%M"
        )
        try:
            start_time = datetime.strptime(start_time_str, "%H:%M").time()
            end_time = datetime.strptime(end_time_str, "%H:%M").time()
        except ValueError:
            raise ValueError(f"Invalid time for leg {sequence}")

        resolved.append(
            {
                "leg": leg,
                "leg_input": leg_input,
                "start_time": start_time,
                "end_time": end_time,
            }
        )

    # leg_inputs arrives in whatever order the client sent it -- sort into
    # travel order before any of the day-offset/chronological-order logic
    # below, which assumes `resolved` already is in travel order.
    resolved.sort(key=lambda item: item["leg"]["sequence"])

    _resolve_day_offsets(resolved)
    _validate_leg_order(resolved)

    new_trips = []  # (sequence, new_trip_id), populated as legs are cloned
    try:
        for item in resolved:
            new_trip_id = duplicate_trip(item["leg"]["trip_id"])
            # Track immediately, before rescheduling -- if a later step in
            # this loop (or the linking below) fails, the except clause
            # needs to know about every trip already created so far.
            new_trips.append((item["leg"]["sequence"], new_trip_id))
            _apply_occurrence_datetime(new_trip_id, anchor_date, item)

        with pg_session() as pg:
            occurrence_id = pg.execute(
                insert_commute_occurrence_query(),
                {"commute_route_id": commute_route_id},
            ).fetchone()[0]
            for sequence, trip_id in new_trips:
                pg.execute(
                    insert_commute_route_trip_query(),
                    {
                        "occurrence_id": occurrence_id,
                        "trip_id": trip_id,
                        "sequence": sequence,
                    },
                )
    except IntegrityError:
        _cleanup_orphaned_trips([trip_id for _, trip_id in new_trips])
        raise ValueError(
            "One or more of these trips is already logged against this commute"
        )
    except Exception:
        _cleanup_orphaned_trips([trip_id for _, trip_id in new_trips])
        raise

    new_trips.sort(key=lambda pair: pair[0])
    logger.info(
        f"Logged occurrence of commute {commute_route_id}: {len(new_trips)} leg(s)"
    )
    return [trip_id for _, trip_id in new_trips]


def _resolve_day_offsets(resolved: list[dict]):
    """
    Annotates each item with day_offset/end_day_offset -- how many days
    after the occurrence's anchor date this leg's start/end fall on.
    Derived from the EFFECTIVE times actually being used (which may differ
    from the template -- a different, later service after a missed
    connection, for instance), not from the template's own schedule, so an
    overnight template leg overridden to a same-day time doesn't still add
    a day, and vice versa.
    """
    day_offset = 0
    prev_end_time = None
    for item in resolved:
        start_time = item["start_time"]
        end_time = item["end_time"]
        if prev_end_time is not None and start_time < prev_end_time:
            day_offset += 1
        item["day_offset"] = day_offset
        if end_time < start_time:
            day_offset += 1
        item["end_day_offset"] = day_offset
        prev_end_time = end_time


def _validate_leg_order(resolved: list[dict]):
    """
    Each leg's actual (day offset + time-of-day + delay) departure must be
    >= its own actual arrival's start, and >= the previous leg's actual
    arrival -- a 0-minute connection is fine, a negative one isn't.
    Expressed in minutes since the occurrence's anchor midnight, so delay
    pushing a leg past midnight falls out of the arithmetic rather than
    needing separate handling.
    """
    prev_actual_arrival = None
    for item in resolved:
        leg_input = item["leg_input"]
        departure_delay = _to_minutes(leg_input.get("departureDelay"))
        arrival_delay = _to_minutes(leg_input.get("arrivalDelay"))

        start_minutes = item["day_offset"] * 1440 + _time_to_minutes(item["start_time"])
        end_minutes = item["end_day_offset"] * 1440 + _time_to_minutes(item["end_time"])
        actual_departure = start_minutes + departure_delay
        actual_arrival = end_minutes + arrival_delay

        sequence = item["leg"]["sequence"]
        if actual_arrival < actual_departure:
            raise ValueError(f"Leg {sequence}: arrival can't be before departure")
        if prev_actual_arrival is not None and actual_departure < prev_actual_arrival:
            raise ValueError(
                f"Leg {sequence}: departs before the previous leg arrives"
            )
        prev_actual_arrival = actual_arrival


def _apply_occurrence_datetime(trip_id: int, anchor_date, item: dict):
    with pg_session() as pg:
        path_result = pg.execute(
            get_user_lines_query(), {"ids": [int(trip_id)]}
        ).fetchone()
        if not path_result or not path_result["path"]:
            # A trip can legitimately have no `paths` row (create_trip skips
            # the insert for an empty route) -- same guard as the other
            # caller of this query, src/plans/import_trips.py.
            sequence = item["leg"]["sequence"]
            raise ValueError(
                f"Leg {sequence}: its trip has no route, so an occurrence"
                " can't be logged for it"
            )
        path = json.loads(path_result["path"])

        trip_row = pg.execute(
            "SELECT * FROM trips WHERE trip_id = :trip_id", {"trip_id": trip_id}
        ).fetchone()
        trip = dict(trip_row._mapping)

    start_date = anchor_date + timedelta(days=item["day_offset"])
    end_date = anchor_date + timedelta(days=item["end_day_offset"])
    new_start = f"{start_date.isoformat()}T{item['start_time'].strftime('%H:%M')}"
    new_end = f"{end_date.isoformat()}T{item['end_time'].strftime('%H:%M')}"

    # processDates only ever looks at the first/last point of the path (for
    # timezone lookup), same as the existing edit-trip form flow.
    limits = [
        {"lat": path[0][0], "lng": path[0][1]},
        {"lat": path[-1][0], "lng": path[-1][1]},
    ]
    _, start_datetime, end_datetime, utc_start_datetime, utc_end_datetime = (
        processDates(
            {
                "precision": "preciseDates",
                "newTripStart": new_start,
                "newTripEnd": new_end,
            },
            limits,
        )
    )

    leg_input = item["leg_input"]
    departure_delay = _minutes_to_seconds(leg_input.get("departureDelay"))
    arrival_delay = _minutes_to_seconds(leg_input.get("arrivalDelay"))

    now = datetime.utcnow()
    updated_trip = Trip(
        trip_id=trip_id,
        username=get_username(trip["user_id"]),
        user_id=trip["user_id"],
        origin_station=trip["origin_station"],
        destination_station=trip["destination_station"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        trip_length=trip["trip_length"],
        estimated_trip_duration=trip["estimated_trip_duration"],
        manual_trip_duration=trip["manual_trip_duration"],
        operator=trip["operator"],
        countries=trip["countries"],
        utc_start_datetime=utc_start_datetime,
        utc_end_datetime=utc_end_datetime,
        created=now,
        last_modified=now,
        line_name=trip["line_name"],
        type=trip["trip_type"],
        material_type=trip["material_type"],
        material_type_advanced=trip["material_type_advanced"],
        seat=trip["seat"],
        reg=trip["reg"],
        waypoints=trip["waypoints"],
        notes=trip["notes"],
        price=trip["price"],
        currency=trip["currency"],
        purchasing_date=trip["purchase_date"],
        ticket_id=trip["ticket_id"],
        path=path,
        is_project=trip["is_project"],
        visibility=trip["visibility"],
        departure_delay=departure_delay,
        arrival_delay=arrival_delay,
        power_type=trip["power_type"],
        co2_override=trip["co2_override"],
        route_source=trip["route_source"],
    )
    update_trip(trip_id, updated_trip)


def _cleanup_orphaned_trips(trip_ids: list[int]):
    """
    duplicate_trip()/update_trip() each manage their own transaction (this
    module can't wrap them in one enclosing transaction without changing
    that shared code), so a failure partway through logging several legs
    can leave earlier legs already fully created. Compensate by deleting
    anything created during this call rather than leaving it stranded.
    """
    if not trip_ids:
        return
    with pg_session() as pg:
        for trip_id in trip_ids:
            pg.execute(delete_trip_query(), {"trip_id": trip_id})
            pg.execute(
                "DELETE FROM paths WHERE trip_id = :trip_id", {"trip_id": trip_id}
            )
    logger.warning(
        f"Cleaned up {len(trip_ids)} orphaned trip(s) after a failed commute log:"
        f" {trip_ids}"
    )


def _time_to_minutes(t) -> int:
    return t.hour * 60 + t.minute


def _to_minutes(value) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _minutes_to_seconds(minutes):
    if minutes in (None, ""):
        return None
    return round(float(minutes) * 60)
