"""Take-off and landing of a flight with a real track, as two timetable stops.

A flight is logged with gate times (off-block, on-block); FR24 knows when it left and
reached the runway, and when it first and last heard the aircraft (about the gates).
Rather than overwrite one with the other, the runway times become stops in the trip's
waypoints, beside the ends, the way a train's intermediate stops are kept. They hold real
times only (dep_rt / arr_rt): there is nothing scheduled about them.

The arrival is when the aircraft was last heard at the gate, early or late; without that,
and for the departure, the trip's times are only corrected where the runway times prove
them wrong. Anything consistent is left as it was.
"""

import json
import math
from datetime import datetime, timezone

from py.utils import getCountryFromCoordinates
from src.utils import _timezone_finder

# FR24 reports 0 ft for an aircraft on the ground.
GROUND_ALT_M = 50

RUNWAY_KINDS = ("takeoff", "landing")

# Stored for surfaces that do not translate stops; the ones that do go by `kind`.
_NAMES = {"takeoff": "Take-off", "landing": "Landing"}


def runway_points(altitude, timestamps):
    """(takeoff_index, landing_index) along the track, each None when it was not seen:
    a trail that starts in the air never saw the take-off, one that ends in the air never
    saw the landing."""
    airborne = [i for i, alt in enumerate(altitude) if alt > GROUND_ALT_M]
    if not airborne:
        return None, None
    first, last = airborne[0], airborne[-1]
    takeoff = first if first > 0 and timestamps[first] else None
    landing = last + 1 if last + 1 < len(altitude) and timestamps[last + 1] else None
    return takeoff, landing


def ground_seen(timestamps, takeoff, landing):
    """(first, last): when FR24 first heard the aircraft before it took off and last heard
    it after it landed, about at the gates, where transponders go on and off. Each None
    when the trail starts or stops at the runway."""
    first = timestamps[0] if takeoff is not None else None
    last = timestamps[-1] if landing is not None else None
    return (
        first if first and timestamps[takeoff] - first >= 60 else None,
        last if last and last - timestamps[landing] >= 60 else None,
    )


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value):
    """A trip's naive UTC time (datetime or 'YYYY-MM-DD HH:MM:SS') as epoch seconds."""
    if not value:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc).timestamp()


def _waypoint(kind, lat, lng, epoch):
    stop = {
        "kind": kind,
        "name": _NAMES[kind],
        "cc": getCountryFromCoordinates(lat, lng)["countryCode"],
        "tz": _timezone_finder.timezone_at(lat=lat, lng=lng),
        "lat": lat,
        "lng": lng,
        "dep_rt" if kind == "takeoff" else "arr_rt": _iso(epoch),
    }
    return {"lat": lat, "lng": lng, "name": stop["name"], "stop": stop}


def is_runway_stop(waypoint):
    return isinstance(waypoint, dict) and (waypoint.get("stop") or {}).get("kind") in RUNWAY_KINDS


def _load(waypoints):
    try:
        waypoints = json.loads(waypoints or "[]")
    except (TypeError, ValueError):
        return []
    return waypoints if isinstance(waypoints, list) else []


def has_runway_stop(waypoints, kind):
    return any(is_runway_stop(w) and w["stop"]["kind"] == kind for w in _load(waypoints))


def track_stops(coords, altitude, timestamps):
    """The take-off and landing the track shows, as waypoints: none, one or both. coords
    are [lat, lng] pairs parallel to altitude/timestamps."""
    takeoff, landing = runway_points(altitude, timestamps)
    # Kept beside the runway times, as the take-off's arrival and the landing's departure:
    # the taxi at each end, which connections count from. Not bounds for the delays, as the
    # runway times are: a transponder can be on a while at the gate.
    first_seen, last_seen = ground_seen(timestamps, takeoff, landing)
    stops = []
    if takeoff is not None:
        stop = _waypoint("takeoff", *coords[takeoff], timestamps[takeoff])
        if first_seen:
            stop["stop"]["arr_rt"] = _iso(first_seen)
        stops.append(stop)
    if landing is not None:
        stop = _waypoint("landing", *coords[landing], timestamps[landing])
        if last_seen:
            stop["stop"]["dep_rt"] = _iso(last_seen)
        stops.append(stop)
    return stops


def _runway_time(stop):
    """The runway time of a runway stop, in epoch seconds, or None."""
    when = stop.get("dep_rt" if stop["kind"] == "takeoff" else "arr_rt")
    return _epoch(when.replace("T", " ").rstrip("Z")) if when else None


def with_stops(waypoints, stops, utc_start, utc_end, departure_delay, arrival_delay):
    """The waypoints JSON with its runway stops replaced by `stops`, but for one that only
    repeats the trip's end beside it: a new trip whose times were filled in from these very
    runway times. Scheduled times typed on the old ones carry over: a track has none."""
    ends = {
        "takeoff": _epoch(utc_start) + (departure_delay or 0) if utc_start else None,
        "landing": _epoch(utc_end) + (arrival_delay or 0) if utc_end else None,
    }
    old = {w["stop"]["kind"]: w["stop"] for w in _load(waypoints) if is_runway_stop(w)}
    kept = [w for w in _load(waypoints) if not is_runway_stop(w)]
    for stop in stops:
        kind, when = stop["stop"]["kind"], _runway_time(stop["stop"])
        for field in ("arr", "dep"):
            if old.get(kind, {}).get(field):
                stop["stop"][field] = old[kind][field]
        if ends[kind] is not None and abs(when - ends[kind]) < 60:
            continue
        if kind == "takeoff":
            kept.insert(0, stop)
        else:
            kept.append(stop)
    return json.dumps(kept, ensure_ascii=False) if kept else None


def delay_fields(waypoints, utc_start, utc_end, departure_delay, arrival_delay):
    """The delays the runway stops correct. A flight reached the gate when it was last heard
    there, transponders going off as the passengers leave: its arrival, early or late.
    Without that, only what the runway times prove: landed after its logged arrival,
    reached the gate at least that late; took off before its logged departure, left it at
    least that early."""
    start, end = _epoch(utc_start), _epoch(utc_end)
    fields = {}
    for w in _load(waypoints):
        if not is_runway_stop(w):
            continue
        kind, when = w["stop"]["kind"], _runway_time(w["stop"])
        if when is None:
            continue
        if kind == "takeoff" and start is not None and when < start + (departure_delay or 0):
            fields["departure_delay"] = math.floor((when - start) / 60) * 60
        if kind == "landing" and end is not None:
            seen = w["stop"].get("dep_rt")
            if seen:
                delay = round((_epoch(seen.replace("T", " ").rstrip("Z")) - end) / 60) * 60
                if delay != (arrival_delay or 0):
                    fields["arrival_delay"] = delay
            elif when > end + (arrival_delay or 0):
                fields["arrival_delay"] = math.ceil((when - end) / 60) * 60
    return fields


def _seconds(delay):
    return float(delay) if delay not in (None, "") else 0


def apply_to_trip(trip):
    """For a Trip saved with a freshly imported FR24 track: its runway stops, from the
    track unless the page already sent them (the edit page shows them as it imports, to be
    put right before saving), and the delays they prove wrong. Corrected without asking:
    importing the flight is the user asking for its real times."""
    if trip.route_source != "fr24" or not trip.altitude or not trip.timestamps:
        return
    start, end = trip.utc_start_datetime, trip.utc_end_datetime
    dep_delay, arr_delay = _seconds(trip.departure_delay), _seconds(trip.arrival_delay)
    if not any(is_runway_stop(w) for w in _load(trip.waypoints)):
        altitude, timestamps = json.loads(trip.altitude), json.loads(trip.timestamps)
        coords = [[p["lat"], p["lng"]] if isinstance(p, dict) else p for p in trip.path]
        if len(coords) != len(altitude) or len(coords) != len(timestamps):
            return
        trip.waypoints = with_stops(trip.waypoints, track_stops(coords, altitude, timestamps),
                                    start, end, dep_delay, arr_delay)
    for name, value in delay_fields(trip.waypoints, start, end, dep_delay, arr_delay).items():
        setattr(trip, name, value)
