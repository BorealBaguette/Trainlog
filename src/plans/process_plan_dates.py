"""Timing materialisation for plan-trips.

A plan-trip is timed either with absolute precision (mirroring the trip builder's
'preciseDates' / 'onlyDate' / 'unknown') or with relative 'day offset + time' for
both departure and arrival. For the relative mode we anchor the day offsets at the
plan's anchor_date (Day 1 = anchor_date) and compute timezone-correct UTC instants
from the path endpoints, so the chained itinerary simulates accurately. The precise
modes delegate to the existing trips processDates() so behaviour stays identical.
"""

from datetime import datetime, time as dtime, timedelta

from src.utils import getUtcDatetime, processDates


def _parse_time(value):
    """'HH:MM' or 'HH:MM:SS' -> datetime.time (None if unparseable)."""
    value = (value or "").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def process_plan_dates(new_trip, new_path, anchor_date):
    """Return the timing column dict for a plan_trip given the builder payload."""
    mode = new_trip.get("precision", "relative")

    if mode == "relative":
        start_day = int(new_trip.get("planStartDay") or 1)
        end_day = int(new_trip.get("planEndDay") or start_day)
        start_time = _parse_time(new_trip.get("planStartTime"))
        end_time = _parse_time(new_trip.get("planEndTime"))
        if start_time is not None and end_time is not None:
            # Timed leg: real local datetimes -> timezone-correct UTC, precise duration.
            local_start = datetime.combine(
                anchor_date + timedelta(days=start_day - 1), start_time
            )
            local_end = datetime.combine(
                anchor_date + timedelta(days=end_day - 1), end_time
            )
            utc_start = getUtcDatetime(dateTime=local_start, **new_path[0])
            utc_end = getUtcDatetime(dateTime=local_end, **new_path[-1])
        else:
            # Untimed leg: just the day(s). Materialise at the date-only marker
            # (00:00:01) so formatTrip shows no clock time; duration falls back to the
            # routed estimate. No UTC (there is no concrete time).
            local_start = datetime.combine(
                anchor_date + timedelta(days=start_day - 1), dtime(0, 0, 1)
            )
            local_end = datetime.combine(
                anchor_date + timedelta(days=end_day - 1), dtime(0, 0, 1)
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
