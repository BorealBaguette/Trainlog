"""Acting on several plan legs at once (the plan page's selection bar), and keeping a
leg's timetable stops in step with its dates.

A leg picked from Transitous carries its stops in `waypoints` (wp.stop: UTC arr/dep,
tz, the run's id in `trip`, live *_rt values). A run id starts with its service day
(`20261102_15:11_feed_trip`), so the same run on another day is the same id with that
day changed. Moving a leg by whole days moves its stops and re-dates the run, keeping
the link Refresh uses; any other move leaves no run to refresh from, so the id goes
(Search finds the new one). Live values always go: they were another day's.
"""

import json
import re
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from src.paths import geom_geojson_to_coords
from src.pg import get_or_create_pg_session
from src.plans.process_plan_dates import process_plan_dates
from src.trips.trip import _strip_tags
from src.utils import getUtcDatetime

_STOP_FMT = "%Y-%m-%dT%H:%M:%SZ"
_RUN_DAY = re.compile(r"^(\d{8})_")
_LIVE_FIELDS = ("arr_rt", "dep_rt", "platform_rt")


def _redate_run(run_id, days):
    m = _RUN_DAY.match(run_id or "")
    if not m:
        return None
    try:
        day = datetime.strptime(m.group(1), "%Y%m%d") + timedelta(days=days)
    except ValueError:
        return None
    return day.strftime("%Y%m%d") + run_id[8:]


def _zone(name):
    try:
        return ZoneInfo(name) if name else None
    except (KeyError, ValueError):
        return None


def _shift_stop_time(value, days, minutes, tz):
    """A stop's UTC 'YYYY-MM-DDTHH:MM:SSZ' moved by `days` calendar days (in its own
    timezone, so a DST change doesn't move the clock time) plus `minutes`."""
    when = datetime.strptime(value, _STOP_FMT)
    if days:
        if tz is not None:
            local = when.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).replace(tzinfo=None)
            local += timedelta(days=days)
            when = local.replace(tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        else:
            when += timedelta(days=days)
    return (when + timedelta(minutes=minutes)).strftime(_STOP_FMT)


def shift_stops(waypoints_json, days=0, minutes=0):
    """`waypoints_json` with its stops moved by `days` calendar days + `minutes`.
    Whole-day moves keep the run (its id re-dated), others drop it. Unchanged (same
    object) when nothing moves or there are no stops."""
    if (not days and not minutes) or not waypoints_json:
        return waypoints_json
    try:
        waypoints = json.loads(waypoints_json)
    except (TypeError, ValueError):
        return waypoints_json
    if not isinstance(waypoints, list):
        return waypoints_json
    changed = False
    for wp in waypoints:
        stop = wp.get("stop") if isinstance(wp, dict) else None
        if not isinstance(stop, dict):
            continue
        changed = True
        tz = _zone(stop.get("tz"))
        for field in ("arr", "dep"):
            try:
                stop[field] = _shift_stop_time(stop[field], days, minutes, tz)
            except (KeyError, TypeError, ValueError):
                pass
        for field in _LIVE_FIELDS:
            stop.pop(field, None)
        if stop.get("trip"):
            run = _redate_run(stop["trip"], days) if not minutes else None
            if run:
                stop["trip"] = run
            else:
                stop.pop("trip")
    if not changed:
        return waypoints_json
    return json.dumps(waypoints, ensure_ascii=False, separators=(",", ":"))


def _first_stop_local(waypoints):
    """Local (naive, in the stop's own timezone) time of the leg's first timed stop."""
    for wp in waypoints:
        stop = wp.get("stop") if isinstance(wp, dict) else None
        if not isinstance(stop, dict):
            continue
        for field in ("dep", "arr"):
            try:
                when = datetime.strptime(stop[field], _STOP_FMT)
            except (KeyError, TypeError, ValueError):
                continue
            tz = _zone(stop.get("tz"))
            if tz is not None:
                when = when.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).replace(tzinfo=None)
            return when
    return None


def align_stops_to_start(waypoints_json, local_start):
    """Stops of a relative (Day N) leg, moved onto the day the leg really starts.

    A relative leg's stops keep the times of whatever day its run was searched on;
    logged on its real date they'd be another day's run (so Refresh would ask
    Transitous for the wrong run). The first stop comes within a day after the
    departure, so the number of whole days to move is the one that puts it there."""
    if not waypoints_json or local_start is None:
        return waypoints_json
    try:
        waypoints = json.loads(waypoints_json)
    except (TypeError, ValueError):
        return waypoints_json
    if not isinstance(waypoints, list):
        return waypoints_json
    first = _first_stop_local(waypoints)
    if first is None:
        return waypoints_json
    gap = first - local_start
    days = -(gap // timedelta(days=1))
    # An untimed leg starts at 00:00:01: same rule, the stop lands on its day.
    return shift_stops(waypoints_json, days=days)


# ── Bulk edit ─────────────────────────────────────────────────────────────────────

# Columns the bulk editor may set, with whether their text is stripped of tags.
BULK_TEXT_FIELDS = ("operator", "line_name", "material_type", "reg", "seat", "notes")
BULK_VISIBILITIES = ("public", "friends", "private")


class BulkError(ValueError):
    """A request the bulk editor refuses as a whole (nothing is written)."""


def _endpoints(geojson):
    coords = geom_geojson_to_coords(geojson)
    if not coords:
        return None, None
    return {"lat": coords[0][0], "lng": coords[0][1]}, {"lat": coords[-1][0], "lng": coords[-1][1]}


def _shifted_timing(pt, minutes):
    """New timing columns + waypoints for a leg moved by `minutes`, or None when the
    leg has nothing to move (undated). Raises BulkError when a relative leg would end
    up before Day 1."""
    days, rest = int(minutes / 1440), minutes - int(minutes / 1440) * 1440
    mode = pt["timing_mode"]
    start, end = _endpoints(pt["geojson"])
    path = [start, end] if start else [{"lat": 0.0, "lng": 0.0}] * 2

    if mode == "relative" and pt["start_day"] is not None:
        end_day = pt["end_day"] or pt["start_day"]
        if pt["start_time"] is not None and pt["end_time"] is not None:
            def move(day, t):
                total = (day - 1) * 1440 + t.hour * 60 + t.minute + minutes
                d, m = divmod(total, 1440)
                return d + 1, dtime(m // 60, m % 60)
            sd, st = move(pt["start_day"], pt["start_time"])
            ed, et = move(end_day, pt["end_time"])
            form = {"precision": "relative", "planStartDay": sd, "planEndDay": ed,
                    "planStartTime": st.strftime("%H:%M"), "planEndTime": et.strftime("%H:%M"),
                    "planWeekdays": pt["weekdays"]}
            # Day N is not a date: only a move within the day changes the stops' times.
            waypoints = shift_stops(pt["waypoints"], minutes=rest) if rest else pt["waypoints"]
        else:
            if not days:
                return None
            sd, ed = pt["start_day"] + days, end_day + days
            form = {"precision": "relative", "planStartDay": sd, "planEndDay": ed,
                    "planWeekdays": pt["weekdays"],
                    "onlyDateDuration": pt["manual_trip_duration"] or ""}
            waypoints = pt["waypoints"]
        if sd < 1:
            raise BulkError("before_day_1")
        timing = process_plan_dates(form, path)
        return timing, waypoints

    if pt["start_datetime"] is None:
        return None
    if mode == "precise" or mode == "preciseDates":
        sdt = pt["start_datetime"] + timedelta(days=days, minutes=rest)
        edt = (pt["end_datetime"] or pt["start_datetime"]) + timedelta(days=days, minutes=rest)
        usdt = getUtcDatetime(dateTime=sdt, **start) if start else pt["utc_start_datetime"]
        uedt = getUtcDatetime(dateTime=edt, **end) if end else pt["utc_end_datetime"]
        waypoints = shift_stops(pt["waypoints"], days=days, minutes=rest)
    else:
        # onlyDate (a day, no clock time): whole days only.
        if not days:
            return None
        sdt = pt["start_datetime"] + timedelta(days=days)
        edt = (pt["end_datetime"] or pt["start_datetime"]) + timedelta(days=days)
        usdt = pt["utc_start_datetime"] + timedelta(days=days) if pt["utc_start_datetime"] else None
        uedt = pt["utc_end_datetime"] + timedelta(days=days) if pt["utc_end_datetime"] else None
        waypoints = shift_stops(pt["waypoints"], days=days)
    timing = {
        "timing_mode": mode, "start_day": None, "end_day": None, "start_time": None,
        "end_time": None, "weekdays": None, "start_datetime": sdt, "end_datetime": edt,
        "utc_start_datetime": usdt, "utc_end_datetime": uedt,
        "manual_trip_duration": pt["manual_trip_duration"],
    }
    return timing, waypoints


def bulk_edit_plan_trips(plan, uids, fields=None, notes_append=False, shift_minutes=0,
                         booked=None, cost_id=None, pg_session=None):
    """Apply one edit to several legs of `plan` (already ownership-checked).

    `fields`: {column: value} from BULK_TEXT_FIELDS + "visibility" ('' clears a text
    field). `booked`: True/False to set, None to leave. `cost_id`: a shared cost uid,
    0 to detach, None to leave. `shift_minutes` moves the legs' times (see
    _shifted_timing). Legs already logged as trips are left alone. Returns how many
    legs changed; raises BulkError (nothing written) on a bad request."""
    fields = {k: v for k, v in (fields or {}).items()
              if k in BULK_TEXT_FIELDS or k == "visibility"}
    if "visibility" in fields and fields["visibility"] not in BULK_VISIBILITIES:
        raise BulkError("visibility")
    uids = [int(u) for u in uids]
    now = datetime.now()
    changed = 0
    with get_or_create_pg_session(pg_session) as pg:
        if cost_id:
            ok = pg.execute(
                "SELECT 1 FROM plan_costs WHERE uid = :uid AND plan_id = :pid",
                {"uid": int(cost_id), "pid": plan["uid"]},
            ).fetchone()
            if not ok:
                raise BulkError("cost")
        rows = pg.execute(
            "SELECT *, ST_AsGeoJSON(geom) AS geojson FROM plan_trips"
            " WHERE plan_id = :pid AND uid = ANY(:uids) AND validated_trip_id IS NULL"
            " AND linked_trip_id IS NULL",
            {"pid": plan["uid"], "uids": uids},
        ).fetchall()
        # Work out every move first: one leg that can't move refuses the whole batch.
        moves = {}
        if shift_minutes:
            for r in rows:
                pt = r._mapping
                moved = _shifted_timing(pt, shift_minutes)
                if moved:
                    moves[pt["uid"]] = moved
        for r in rows:
            pt = r._mapping
            sets, params = ["last_modified = :now"], {"now": now, "uid": pt["uid"], "pid": plan["uid"]}
            for col, val in fields.items():
                val = _strip_tags(val) if col in BULK_TEXT_FIELDS and val else val
                if col == "notes" and notes_append and val:
                    sets.append("notes = CASE WHEN COALESCE(notes, '') = '' THEN :notes"
                                " ELSE notes || chr(10) || :notes END")
                else:
                    sets.append(f"{col} = :{col}")
                params[col] = val if val not in ("", None) else None
            if booked is not None:
                sets.append("booked = :booked")
                params["booked"] = bool(booked)
            if cost_id is not None:
                sets.append("cost_id = :cost_id")
                params["cost_id"] = int(cost_id) or None
            if pt["uid"] in moves:
                timing, waypoints = moves[pt["uid"]]
                for col in ("timing_mode", "start_day", "end_day", "start_time", "end_time",
                            "weekdays", "start_datetime", "end_datetime", "utc_start_datetime",
                            "utc_end_datetime", "manual_trip_duration"):
                    sets.append(f"{col} = :t_{col}")
                    params[f"t_{col}"] = timing[col]
                sets.append("waypoints = :waypoints")
                params["waypoints"] = waypoints
            if len(sets) == 1:
                continue
            pg.execute(
                f"UPDATE plan_trips SET {', '.join(sets)} WHERE uid = :uid AND plan_id = :pid",
                params,
            )
            changed += 1
    return changed


def bulk_delete_plan_trips(plan, uids, pg_session=None):
    """Remove legs from the plan. A linked trip is only unlinked: the trip stays."""
    with get_or_create_pg_session(pg_session) as pg:
        return pg.execute(
            "DELETE FROM plan_trips WHERE plan_id = :pid AND uid = ANY(:uids)",
            {"pid": plan["uid"], "uids": [int(u) for u in uids]},
        ).rowcount


def bulk_duplicate_plan_trips(plan, uids, pg_session=None):
    """Copy legs within the plan (a return trip, the same train another day). The
    copies are plain plan legs: not logged, whatever the originals were. A linked
    trip is in a plan once, so it isn't copied."""
    from src.plans.duplicate_plan import _PLAN_TRIP_COPY_COLS

    now = datetime.now()
    with get_or_create_pg_session(pg_session) as pg:
        return pg.execute(
            f"""
            INSERT INTO plan_trips ({_PLAN_TRIP_COPY_COLS}, user_id, plan_id, cost_id, created, last_modified)
            SELECT {_PLAN_TRIP_COPY_COLS}, user_id, plan_id, cost_id, :now, :now
            FROM plan_trips
            WHERE plan_id = :pid AND uid = ANY(:uids) AND linked_trip_id IS NULL
            ORDER BY sort_order, uid
            """,
            {"pid": plan["uid"], "uids": [int(u) for u in uids], "now": now},
        ).rowcount
