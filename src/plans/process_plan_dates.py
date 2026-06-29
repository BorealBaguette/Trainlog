"""Timing materialisation for plan-trips.

A plan-trip is timed either with absolute precision (mirroring the trip builder's
'preciseDates' / 'onlyDate' / 'unknown') or with relative 'day offset + time' for
both departure and arrival.

Relative ("Day N") legs are deliberately NOT tied to any real calendar date: only the
anchor chosen at save time (validate_plan) turns them into dated trips. For storage and
the in-plan simulation we materialise them against a fixed internal reference date, so
a leg's stored datetimes depend only on (day offset, time, location) — never on the
plan's anchor_date. (Anchoring relative legs at the plan's anchor_date used to make
legs drift apart whenever the anchor changed between saves.) The day offset and clock
time live in their own durable columns (start_day/end_day/start_time/end_time) and are
the real source of truth. The precise modes delegate to the existing trips
processDates() so behaviour stays identical.
"""

from datetime import date, datetime, time as dtime, timedelta

from src.utils import getUtcDatetime, processDates

# Fixed internal anchor for relative legs (Day 1). Arbitrary — only the time-of-day,
# the day offset and the timezone-correct elapsed/UTC ordering it produces are
# meaningful; the calendar date itself is a placeholder.
RELATIVE_REF_DATE = date(2000, 1, 1)


def _parse_time(value):
    """'HH:MM' or 'HH:MM:SS' -> datetime.time (None if unparseable)."""
    value = (value or "").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def process_plan_dates(new_trip, new_path):
    """Return the timing column dict for a plan_trip given the builder payload."""
    mode = new_trip.get("precision", "relative")

    if mode == "relative":
        start_day = int(new_trip.get("planStartDay") or 1)
        end_day = int(new_trip.get("planEndDay") or start_day)
        start_time = _parse_time(new_trip.get("planStartTime"))
        end_time = _parse_time(new_trip.get("planEndTime"))
        if start_time is not None and end_time is not None:
            # Timed leg: local datetimes on the fixed reference date -> timezone-correct
            # UTC (so the elapsed duration, and the cross-leg ordering, are right even
            # across timezone boundaries), independent of any anchor_date.
            local_start = datetime.combine(
                RELATIVE_REF_DATE + timedelta(days=start_day - 1), start_time
            )
            local_end = datetime.combine(
                RELATIVE_REF_DATE + timedelta(days=end_day - 1), end_time
            )
            utc_start = getUtcDatetime(dateTime=local_start, **new_path[0])
            utc_end = getUtcDatetime(dateTime=local_end, **new_path[-1])
        else:
            # Untimed leg: just the day(s). Materialise at the date-only marker
            # (00:00:01) so formatTrip shows no clock time; duration falls back to the
            # routed estimate. No UTC (there is no concrete time).
            local_start = datetime.combine(
                RELATIVE_REF_DATE + timedelta(days=start_day - 1), dtime(0, 0, 1)
            )
            local_end = datetime.combine(
                RELATIVE_REF_DATE + timedelta(days=end_day - 1), dtime(0, 0, 1)
            )
            utc_start = utc_end = None
            start_time = end_time = None
        return {
            "timing_mode": "relative",
            "start_day": start_day,
            "end_day": end_day,
            "start_time": start_time,
            "end_time": end_time,
            "start_datetime": local_start,
            "end_datetime": local_end,
            "utc_start_datetime": utc_start,
            "utc_end_datetime": utc_end,
            "manual_trip_duration": None,
        }

    # preciseDates / onlyDate / unknown -> reuse trips date logic verbatim.
    man, sdt, edt, usdt, uedt = processDates(new_trip, new_path)
    return {
        "timing_mode": mode,
        "start_day": None,
        "end_day": None,
        "start_time": None,
        "end_time": None,
        "start_datetime": sdt if sdt not in [-1, 1] else None,
        "end_datetime": edt if edt not in [-1, 1] else None,
        "utc_start_datetime": usdt,
        "utc_end_datetime": uedt,
        "manual_trip_duration": man,
    }
