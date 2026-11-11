"""Take-off and landing of a flight with a real track, as two timetable stops.

A flight is logged with gate times (off-block, on-block); FR24 knows when it left and
reached the runway, and when it first and last heard the aircraft (about the gates).
Rather than overwrite one with the other, the runway times become stops in the trip's
waypoints, beside the ends, the way a train's intermediate stops are kept. They hold real
times only (dep_rt / arr_rt): there is nothing scheduled about them.

The arrival is when the aircraft was last heard at the gate, early or late; without that,
and for the departure, the trip's times are only corrected where the runway times prove
them wrong. Anything consistent is left as it was.

Beside them, a flight can carry milestones the user types in (boarding, pushback, de-icing,
holding…): stops of kind "milestone" with a free name, a start (arr_rt) and optional end
(dep_rt), placed at the departure airport, in flight or at the arrival airport. They are
the user's own record, so a new track keeps them, and they move with the flight's day.
"""

import json
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from geopy.distance import geodesic

from py.utils import getCountryFromCoordinates
from src.pg import pg_session
from src.utils import _timezone_finder

# FR24 reports 0 ft for an aircraft on the ground.
GROUND_ALT_M = 50

RUNWAY_KINDS = ("takeoff", "landing")
MILESTONE = "milestone"
# Where a milestone happened: its position, and the time zone its times are typed in (the
# departure airport's in flight).
MILESTONE_PLACES = ("departure", "in_flight", "arrival")
MILESTONE_NAME_MAX = 60

# How far past where the altitude puts a landing its braking is looked for.
RUNWAY_SEARCH_S = 120

# Stored for surfaces that do not translate stops; the ones that do go by `kind`.
_NAMES = {"takeoff": "Take-off", "landing": "Landing"}


def runway_points(coords, altitude, timestamps):
    """(takeoff_index, landing_index) along the track, each None when it was not seen:
    a trail that starts in the air never saw the take-off, one that ends in the air never
    saw the landing.

    Altitude is barometric, and on a low-pressure day reads 0 well short of the runway: it
    only says whether they were seen. Lift-off is the first reading off 0 (FR24 gives 0
    while the aircraft reports itself on the ground), touchdown where the braking starts:
    the ground flag comes too late there, the reading already 0 on the approach."""
    airborne = [i for i, alt in enumerate(altitude) if alt > GROUND_ALT_M]
    if not airborne:
        return None, None
    first, last = airborne[0], airborne[-1]
    takeoff = first if first > 0 and timestamps[first] else None
    landing = last + 1 if last + 1 < len(altitude) and timestamps[last + 1] else None
    while takeoff and altitude[takeoff - 1] > 0 and timestamps[takeoff - 1]:
        takeoff -= 1
    if landing is not None:
        # Speeds only about the touchdown (the minute before it, the search after it): a
        # long-haul's whole track would be thousands of geodesics for nothing.
        t_land, lo, hi = timestamps[landing], landing, landing
        while lo > 0 and timestamps[lo - 1] and t_land - timestamps[lo - 1] <= 60:
            lo -= 1
        while (hi + 1 < len(timestamps) and timestamps[hi + 1]
               and timestamps[hi + 1] - t_land <= RUNWAY_SEARCH_S):
            hi += 1
        speed = _speeds(coords, timestamps, lo, hi + 2)
        approach = _approach_speed(speed, timestamps, landing)
        i = landing
        while (approach and i + 1 < len(speed) and speed[i] and speed[i] >= 0.85 * approach
               and timestamps[i + 1] and timestamps[i + 1] - t_land <= RUNWAY_SEARCH_S):
            i += 1
        landing = i if approach and speed[i] and speed[i] < 0.85 * approach else landing
    return takeoff, landing


def _speeds(coords, timestamps, lo=0, hi=None):
    """m/s from each fix (of lo…hi, the others None) to the first one at least 5 s on
    (fixes a second apart are too jittery to tell), None where there is none."""
    out = [None] * len(timestamps)
    for i in range(lo, min(len(timestamps), len(timestamps) if hi is None else hi)):
        t = timestamps[i]
        if not t:
            continue
        j = next((j for j in range(i + 1, len(timestamps))
                  if timestamps[j] and timestamps[j] - t >= 5), None)
        out[i] = geodesic(coords[i], coords[j]).meters / (timestamps[j] - t) if j else None
    return out


def _approach_speed(speed, timestamps, index):
    """The median speed over the minute before `index`."""
    picked, i = [], index
    while i >= 0 and timestamps[i] and timestamps[index] - timestamps[i] <= 60:
        if speed[i]:
            picked.append(speed[i])
        i -= 1
    return sorted(picked)[len(picked) // 2] if picked else None


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


def is_milestone(waypoint):
    return isinstance(waypoint, dict) and (waypoint.get("stop") or {}).get("kind") == MILESTONE


def _stop_epoch(stop):
    """When a stop happened, for ordering: a runway stop's runway time, else its first time."""
    kind = stop.get("kind")
    fields = ("dep_rt", "arr_rt") if kind == "takeoff" else ("arr_rt", "dep_rt", "arr", "dep")
    when = next((stop[f] for f in fields if stop.get(f)), None)
    try:
        return _epoch(when.replace("T", " ").rstrip("Z")) if when else None
    except ValueError:
        return None


def in_time_order(waypoints):
    """The waypoints with their timed stops in time order, each in one of the places the
    timed stops held (a point without a time stays where it was)."""
    slots = [i for i, w in enumerate(waypoints)
             if isinstance(w, dict) and isinstance(w.get("stop"), dict) and _stop_epoch(w["stop"]) is not None]
    ordered = sorted((waypoints[i] for i in slots), key=lambda w: _stop_epoch(w["stop"]))
    out = list(waypoints)
    for i, w in zip(slots, ordered):
        out[i] = w
    return out


def _interpolate(path, fraction):
    """The [lat, lng] `fraction` of the way along `path` ([lat, lng] pairs), by distance."""
    if len(path) < 2:
        return list(path[0])

    # Only relative lengths matter here: flat-earth legs, cheap on a long FR24 track
    def leg(a, b):
        dx = (b[1] - a[1] + 540) % 360 - 180
        return math.hypot(b[0] - a[0], dx * math.cos(math.radians((a[0] + b[0]) / 2)))
    legs = [leg(a, b) for a, b in zip(path, path[1:])]
    target, run = sum(legs) * fraction, 0.0
    for (a, b), leg in zip(zip(path, path[1:]), legs):
        if leg and run + leg >= target:
            k = (target - run) / leg
            lng = a[1] + ((b[1] - a[1] + 540) % 360 - 180) * k   # the short way, past 180° too
            return [a[0] + (b[0] - a[0]) * k, (lng + 540) % 360 - 180]
        run += leg
    return list(path[-1])


def milestone_waypoint(name, place, start, end, path, window=None):
    """A milestone as a waypoint. `start`/`end` are naive local datetimes (end optional),
    in the departure airport's time zone unless `place` is "arrival"; `path` the flight's
    [lat, lng] points; `window` (utc start, utc end epochs) the flight, for where along the
    path one in flight goes. Raises ValueError for a bad milestone."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A milestone needs a name.")
    if len(name) > MILESTONE_NAME_MAX:
        raise ValueError(f"A milestone's name is at most {MILESTONE_NAME_MAX} characters.")
    if place not in MILESTONE_PLACES:
        raise ValueError(f"A milestone's place is one of {list(MILESTONE_PLACES)}.")
    anchor = path[-1] if place == "arrival" else path[0]
    tz = _timezone_finder.timezone_at(lat=anchor[0], lng=anchor[1]) or "UTC"

    def utc(local):
        return local.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)

    start_utc = utc(start)
    end_utc = utc(end) if end else None
    if end_utc and end_utc < start_utc:
        raise ValueError(f"Milestone {name!r} ends before it starts.")
    if place == "in_flight":
        t0, t1 = window or (None, None)
        k = 0.5 if not (t0 and t1 and t1 > t0) else (start_utc.timestamp() - t0) / (t1 - t0)
        lat, lng = _interpolate(path, min(max(k, 0.02), 0.98))
    else:
        lat, lng = anchor
    stop = {
        "kind": MILESTONE, "name": name, "place": place, "tz": tz,
        "cc": getCountryFromCoordinates(lat, lng)["countryCode"],
        "lat": lat, "lng": lng, "arr_rt": _iso(start_utc.timestamp()),
    }
    if end_utc and end_utc != start_utc:
        stop["dep_rt"] = _iso(end_utc.timestamp())
    return {"lat": lat, "lng": lng, "name": name, "stop": stop}


def with_milestones(waypoints, milestones):
    """The waypoints JSON with its milestones replaced by `milestones` (waypoints), in
    time order with the runway stops."""
    kept = [w for w in _load(waypoints) if not is_milestone(w)] + list(milestones)
    kept = in_time_order(kept)
    return json.dumps(kept, ensure_ascii=False) if kept else None


def shift_milestone(stop, delta):
    """A milestone moved by `delta` (timedelta) with its flight."""
    for field in ("arr_rt", "dep_rt"):
        try:
            when = datetime.strptime(stop[field], "%Y-%m-%dT%H:%M:%SZ")
            stop[field] = (when + delta).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, TypeError, ValueError):
            pass


def _load(waypoints):
    try:
        waypoints = json.loads(waypoints or "[]")
    except (TypeError, ValueError):
        return []
    return waypoints if isinstance(waypoints, list) else []


def has_runway_stop(waypoints, kind):
    return any(is_runway_stop(w) and w["stop"]["kind"] == kind for w in _load(waypoints))


def airport_position(icao):
    """[lat, lng] of the airport `icao`, or None."""
    if not icao:
        return None
    with pg_session() as pg:
        airport = pg.execute(
            "SELECT latitude, longitude FROM airports WHERE ident = :icao", {"icao": icao}
        ).fetchone()
    return list(airport) if airport else None


def land_short_track(coords, altitude, timestamps, dest, landed_at):
    """A track that stops short of the runway, finished in place at `dest` ([lat, lng]) on
    the ground at `landed_at`, when and where FR24's summary says it landed."""
    if dest and landed_at and runway_points(coords, altitude, timestamps)[1] is None:
        coords.append(dest)
        altitude.append(0)
        timestamps.append(landed_at)


def track_stops(coords, altitude, timestamps):
    """The take-off and landing the track shows, as waypoints: none, one or both. coords
    are [lat, lng] pairs parallel to altitude/timestamps."""
    takeoff, landing = runway_points(coords, altitude, timestamps)
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
    # The user's milestones (boarding before the take-off, …) where their times put them
    kept = in_time_order(kept)
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
        # An estimate (the landing of a flight lost on its approach) proves nothing
        if when is None or w["stop"].get("estimated"):
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


def with_seen(stops, first_seen, last_seen):
    """The stops with FR24's summary first and last seen (UTC ISO) as the transponder
    times, rather than the track's ends: they are what a new trip's own times were filled
    in from (new and compose), so the arrival reads on time to the minute."""
    seen = {"takeoff": ("arr_rt", first_seen, "dep_rt"), "landing": ("dep_rt", last_seen, "arr_rt")}
    for stop in stops:
        st = stop["stop"]
        field, when, runway = seen[st["kind"]]
        if not when or not st.get(runway):
            continue
        when = _iso(_epoch(when.replace("T", " ").rstrip("Z")))
        # Before the take-off, after the landing: else it was heard in the air
        if (when < st[runway]) == (st["kind"] == "takeoff") and when != st[runway]:
            st[field] = when
    return stops


def apply_to_trip(trip, first_seen=None, last_seen=None, gate_departure=None, gate_arrival=None):
    """For a Trip saved with a freshly imported FR24 track: its runway stops, from the
    track unless the page already sent them (the edit page shows them as it imports, to be
    put right before saving), and the delays they prove wrong. Corrected without asking:
    importing the flight is the user asking for its real times. first_seen / last_seen are
    FR24's summary times (UTC ISO) the page filled the trip's times from, if it did;
    gate_departure / gate_arrival its gate events (UTC ISO), where FR24 knows them: when
    the flight really left and reached the gate, so they set the delays outright."""
    if trip.route_source != "fr24" or not trip.altitude or not trip.timestamps:
        return
    start, end = trip.utc_start_datetime, trip.utc_end_datetime
    dep_delay, arr_delay = _seconds(trip.departure_delay), _seconds(trip.arrival_delay)
    if not any(is_runway_stop(w) for w in _load(trip.waypoints)):
        altitude, timestamps = json.loads(trip.altitude), json.loads(trip.timestamps)
        coords = [[p["lat"], p["lng"]] if isinstance(p, dict) else p for p in trip.path]
        if len(coords) != len(altitude) or len(coords) != len(timestamps):
            return
        stops = with_seen(track_stops(coords, altitude, timestamps), first_seen, last_seen)
        trip.waypoints = with_stops(trip.waypoints, stops, start, end, dep_delay, arr_delay)
    fields = delay_fields(trip.waypoints, start, end, dep_delay, arr_delay)
    for name, gate, logged, current in (("departure_delay", gate_departure, start, dep_delay),
                                        ("arrival_delay", gate_arrival, end, arr_delay)):
        gate, logged = _epoch(gate.replace("T", " ").rstrip("Z")) if gate else None, _epoch(logged)
        if gate is not None and logged is not None:
            delay = round((gate - logged) / 60) * 60
            fields.pop(name, None)
            if delay != current:
                fields[name] = delay
    for name, value in fields.items():
        setattr(trip, name, value)
