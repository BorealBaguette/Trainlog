"""Timetable suggestions for the new-trip form, from MOTIS (the Transitous instance).

Given the origin and destination the user picked, list the *direct* services between
them around the chosen departure time, already shaped for the form: local times,
the line, the intermediate stops (which become the trip's via waypoints) and the
operator resolved to a Trainlog operator so its logo can be shown.
"""

import contextvars
import json
import logging
import math
import re
import select
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import polyline
import requests
from flask import Blueprint, abort, jsonify, request

from py.utils import getCountryFromCoordinates
from src.pg import pg_session
from src.utils import _timezone_finder, login_required

logger = logging.getLogger(__name__)

motis_blueprint = Blueprint("motis", __name__)

MOTIS_PLAN_URL = "https://api.transitous.org/api/v5/plan"
MOTIS_STOPS_URL = "https://api.transitous.org/api/v1/reverse-geocode"
MOTIS_BOARD_URL = "https://api.transitous.org/api/v5/stoptimes"
MOTIS_TRIP_URL = "https://api.transitous.org/api/v5/trip"
USER_AGENT = "Trainlog/1.0 (https://trainlog.me; admin@trainlog.me; trip form timetable suggestions)"

# Search from a little before the time on the form to a few days after it: the form
# time is often just "now" or a rough guess (the list's Earlier/Later buttons go further,
# see page in _search), and a service that doesn't run every day
# (a train a few days a week) should still show a couple of runs. Busy lines don't
# fill those days: each search returns a count of results (below), so a metro line
# stops after an hour or so, while Oslo–Bergen (6 a day) reaches 4 days ahead.
# Not more: each search returns a count of results, which a busy line fills before even
# reaching the form's time (Tallinn's buses, one every minute or two, ended at 14:15 for
# 14:19 with an hour).
WINDOW_BEFORE = timedelta(minutes=20)
WINDOW_AHEAD = timedelta(days=4)
WINDOW_LENGTH = WINDOW_BEFORE + WINDOW_AHEAD
# The list is never cut before this (see _departures)...
MIN_HORIZON = timedelta(hours=24)
# ...nor longer than this many departures, the earliest kept.
MAX_DEPARTURES = 60
# Ask for a number of results rather than a fixed window: MOTIS starts with a short
# window and widens it until it has found this many itineraries. A fixed 25 h window
# gave anything from 1 to 450 on sparse vs busy lines. Each departure tends to come
# back about twice (walking variants, slowDirect copies, merged below), so this is
# ~20 departures on a busy line. WINDOW_LENGTH caps how far ahead results are kept.
SEARCH_WINDOW = timedelta(hours=1)
ITINERARIES = 40
# Departures read off the origin stop's board per request (see _board_legs). Each
# carries its following stops, so this is a few hundred KB at a busy station.
BOARD_DEPARTURES = 100

# Trainlog trip type -> the MOTIS transit modes that count as that type.
TRANSIT_MODES = {
    "train": "RAIL,HIGHSPEED_RAIL,LONG_DISTANCE,NIGHT_RAIL,REGIONAL_FAST_RAIL,REGIONAL_RAIL,SUBURBAN",
    "metro": "SUBWAY,METRO",
    "tram": "TRAM",
    "bus": "BUS,COACH",
    "ferry": "FERRY",
    "aerialway": "AERIAL_LIFT,CABLE_CAR",
    "funicular": "FUNICULAR",
}
RAIL_MODES = set(TRANSIT_MODES["train"].split(","))

# How far the service's first/last stop may be from the stations on the form. OSM
# station points and GTFS stops of a big station can sit a few hundred metres apart,
# but metro/tram/bus stations are themselves only a few hundred metres apart, so there
# the same reach would accept the next station (Filles du Calvaire for République).
MAX_STOP_DISTANCE_M = 600
URBAN_STOP_DISTANCE_M = 300
URBAN_MODES = {"SUBWAY", "METRO", "TRAM", "BUS", "COACH"}


def _reach(modes):
    return URBAN_STOP_DISTANCE_M if modes <= URBAN_MODES else MAX_STOP_DISTANCE_M
# Fallback searches from timetable stops (see _stop_places): at most this many stops
# per end, so at most (n + 1)^2 - 1 extra requests.
MAX_FALLBACK_STOPS = 2

# The same lookup is fired again whenever the user nudges the date or time back and
# forth, so keep answers for a few minutes rather than asking Transitous each time.
_CACHE_TTL = 300
# Which stops are near a station doesn't change from one search to the next.
_STOPS_CACHE_TTL = 24 * 3600
_CACHE_MAX = 256
_cache = {}

# Searches start on the 10 minutes before the form's time minus WINDOW_BEFORE, so moving
# the time by a few minutes asks Transitous the same thing again (and hits the cache).
# Not coarser: on a busy line every minute of lead is results that never reach the time.
START_STEP = timedelta(minutes=10)

# Transitous refuses bursts from one address: 6 plan requests at once got half of them a
# 429 straight away (no Retry-After), while 3 at once or back to back went through. So
# this process keeps at most this many plan/board requests in flight (stop lookups are
# cheap, and went through 6 at once), and retries a refused one once after a pause.
TRANSITOUS_PARALLEL = 2
_transitous_slots = threading.BoundedSemaphore(TRANSITOUS_PARALLEL)
RETRY_AFTER_429_S = 1.5
# Refused again: no requests from this process for a while (Retry-After, or this long).
RATE_LIMIT_BACKOFF_S = 60
_backoff_until = 0.0


# Transitous only loads timetables from about a month back to a year ahead, and says so
# when asked outside them ("query time … is outside of loaded timetable window
# [2026-09-02 00:00, 2027-10-03 00:00["). That window is remembered for a few hours, so
# a trip logged for a past year doesn't ask at all. It moves by a day each day.
_TIMETABLE_WINDOW_RE = re.compile(r"outside of loaded timetable window \[([\d-]+ [\d:]+), ([\d-]+ [\d:]+)\[")
TIMETABLE_WINDOW_TTL_S = 6 * 3600
_timetable_window = None  # (first, end) as UTC datetimes, and when to forget it


class OutsideTimetable(requests.RequestException):
    """The search is for a time Transitous has no timetables for."""


def _outside_timetable(start, window):
    """Whether a search from `start` over `window` falls wholly outside the timetables
    Transitous has loaded, as far as it last said."""
    if not _timetable_window or _timetable_window[2] < time.monotonic():
        return False
    first, end, _ = _timetable_window
    return start + window < first or start >= end


def _note_timetable_window(response):
    """Remember the loaded timetable window from a 400 that names it; whether it did."""
    global _timetable_window
    try:
        match = _TIMETABLE_WINDOW_RE.search(response.json().get("error", ""))
    except ValueError:
        return False
    if not match:
        return False
    first, end = (
        datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        for value in match.groups()
    )
    _timetable_window = (first, end, time.monotonic() + TIMETABLE_WINDOW_TTL_S)
    return True


class RateLimited(requests.RequestException):
    """Transitous asked us to slow down; nothing is sent until the backoff ends."""


# A search the browser has given up on (it aborts the request when the form changes)
# stops asking Transitous: the worker would otherwise stay on it for up to ~10 s, and a
# sync worker serves nothing else meanwhile, the user's next search included.
class Cancelled(Exception):
    """The request this search is for has gone away."""


# The current search's cancel flag, seen by _fetch in the pool threads (see _submit).
_cancel = contextvars.ContextVar("motis_cancel", default=None)
# How often the request thread checks whether the browser is still there.
CANCEL_POLL_S = 0.25


# Legal-form suffixes GTFS agency names carry and Trainlog operator names do not.
_LEGAL_SUFFIX = re.compile(
    r"[\s,]+(AG|GmbH|SA|S\.A\.|SAS|S\.p\.A\.|SpA|Ltd\.?|Limited|plc|Inc\.?|BV|B\.V\.|NV|N\.V\.|AB|AS|A/S|Oy|a\.s\.|s\.r\.o\.|sp\. z o\.o\.)$",
    re.IGNORECASE,
)


def _parse_coords(value):
    try:
        lat, lng = (float(x) for x in value.split(","))
    except (AttributeError, ValueError):
        abort(400)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        abort(400)
    return lat, lng


def _tz(lat, lng):
    name = _timezone_finder.timezone_at(lat=lat, lng=lng)
    return ZoneInfo(name) if name else timezone.utc


def _parse_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _local(value, tz):
    """'YYYY-MM-DDTHH:MM' wall-clock time at the stop, which is what the form takes."""
    dt = _parse_utc(value)
    return dt.astimezone(tz).strftime("%Y-%m-%dT%H:%M") if dt else None


def _operator_candidates(agency):
    """Spellings to try, most specific first: 'Schweizerische Bundesbahnen SBB' is
    known to Trainlog as 'SBB', 'DB Fernverkehr AG' as 'DB Fernverkehr' or 'DB'."""
    agency = agency.strip()
    stripped = _LEGAL_SUFFIX.sub("", agency).strip()
    words = stripped.split()
    candidates = [agency, stripped]
    paren = re.search(r"\(([^)]+)\)\s*$", stripped)
    if paren:
        candidates += [paren.group(1), stripped[: paren.start()].strip()]
    if len(words) > 1:
        candidates += [words[-1], words[0]]
    return list(dict.fromkeys(c for c in candidates if c))


def _resolve_operators(agencies, on_date):
    """{agency name -> {"operator": short_name, "logo_url": url | None}} for the
    agencies an operator alias matches. Unmatched agencies are left out."""
    names, owners = [], []
    for agency in agencies:
        for rank, candidate in enumerate(_operator_candidates(agency)):
            names.append(candidate)
            owners.append((agency, rank))
    if not names:
        return {}

    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT c.ord, o.short_name, l.logo_url
            FROM unnest(CAST(:names AS text[])) WITH ORDINALITY AS c(name, ord)
            JOIN operator_aliases a
              ON a.operator_type = 'operator'
             AND a.normalized = operator_normalize(c.name)
            JOIN operators o ON o.operator_id = a.operator_id
            LEFT JOIN LATERAL (
                SELECT logo_url FROM operator_logos
                WHERE operator_id = o.operator_id
                  AND (effective_date IS NULL OR effective_date <= :on_date)
                ORDER BY effective_date DESC NULLS LAST, uid DESC
                LIMIT 1
            ) l ON TRUE
            """,
            {"names": names, "on_date": on_date},
        ).fetchall()

    best = {}
    for row in rows:
        agency, rank = owners[row["ord"] - 1]
        if agency not in best or rank < best[agency][0]:
            best[agency] = (rank, {"operator": row["short_name"], "logo_url": row["logo_url"]})
    return {agency: info for agency, (_, info) in best.items()}


def _line_name(leg):
    line = (leg.get("displayName") or leg.get("routeShortName") or "").strip()
    number = (leg.get("tripShortName") or "").strip()
    # Rail services are known by their train number (TGV 6123, IC 1810); elsewhere
    # tripShortName tends to be an internal id, so it is left out.
    if (
        leg.get("mode") in RAIL_MODES
        # Train numbers only (6643, 1808): RER mission codes (QIWI58) are not. And not on
        # lettered suburban lines (RER A, Transilien L), whose numbers mean nothing to riders.
        and re.fullmatch(r"\d{1,6}", number)
        and not re.fullmatch(r"[A-Z]{1,2}", line)
        and number.strip("0")
        and number not in line.split()
        and not line.endswith(number)
    ):
        line = f"{line} {number}".strip()
    # A lettered line whose agency is a network acronym is known by both ("RER A");
    # other lettered lines get the translated "Line" in the form.
    agency = (leg.get("agencyName") or "").strip()
    if re.fullmatch(r"[A-Z]{1,2}", line) and re.fullmatch(r"[A-Z]{2,5}", agency):
        line = f"{agency} {line}"
    return line


# Coordinates go out to 6 decimals (~10 cm): they end up in the router's URL, one per
# stop, and a long bus run at full float precision outgrew gunicorn's request line.
COORD_DIGITS = 6


def _utc(value):
    """MOTIS's ISO time as 'YYYY-MM-DDTHH:MM:SSZ', or None."""
    dt = _parse_utc(value)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _stop(stop):
    """[lat, lng, name, country code, arrival 'HH:MM', departure 'HH:MM', platform,
    scheduled arrival UTC, scheduled departure UTC, time zone, stop lat, stop lng,
    Transitous stop id, live arrival UTC, live departure UTC, scheduled platform].

    The country so the form can flag it like a picked station; the local times as shown
    there; the platform (track) when the feed has it, the real-time one if it changed.
    The rest is what the trip keeps of the stop (the waypoint's `stop`, see routing.js):
    UTC times, for the live map and current trip, the zone to show them in local time
    again, and the stop's own position, which [0]/[1] leave once moved onto the run's
    path (_snap_stops), and its id, to match it against Transitous later. The live times
    only when the feed has them and they differ from the schedule; the scheduled platform
    beside the live one shown in [6]."""
    tz = ZoneInfo(stop["tz"]) if stop.get("tz") else _tz(stop["lat"], stop["lon"])

    def clock(field):
        value = _local(stop.get("scheduled" + field) or stop.get(field.lower()), tz)
        return value[11:] if value else None

    return [
        round(stop["lat"], COORD_DIGITS),
        round(stop["lon"], COORD_DIGITS),
        stop.get("name", ""),
        getCountryFromCoordinates(stop["lat"], stop["lon"])["countryCode"],
        clock("Arrival"),
        clock("Departure"),
        (stop.get("track") or stop.get("scheduledTrack") or "").strip() or None,
        _utc(stop.get("scheduledArrival") or stop.get("arrival")),
        _utc(stop.get("scheduledDeparture") or stop.get("departure")),
        getattr(tz, "key", None),
        round(stop["lat"], COORD_DIGITS),
        round(stop["lon"], COORD_DIGITS),
        stop.get("stopId"),
        _live(stop, "Arrival"),
        _live(stop, "Departure"),
        (stop.get("scheduledTrack") or "").strip() or None,
    ]


def _live(stop, field):
    """The stop's live arrival/departure (UTC), when it differs from the scheduled one."""
    live, scheduled = _utc(stop.get(field.lower())), _utc(stop.get("scheduled" + field))
    return live if live and scheduled and live != scheduled else None


def _distance_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * 6371000 * math.asin(math.sqrt(a))


def _direct_leg(itinerary, modes, origin, destination):
    """The one leg of the itinerary that runs from the origin to the destination
    station on a mode of the trip's type, or None.

    Walking legs around it are how MOTIS got from the coordinates to its stops."""
    legs = [
        leg
        for leg in itinerary.get("legs", [])
        if leg.get("mode") in modes
        and _distance_m(leg["from"]["lat"], leg["from"]["lon"], *origin) <= _reach(modes)
        and _distance_m(leg["to"]["lat"], leg["to"]["lon"], *destination) <= _reach(modes)
    ]
    return legs[0] if len(legs) == 1 else None


def _board_legs(stop_id, modes, start, destination):
    """Direct services from the origin stop's departure board: those that later call
    within reach (_reach) of the destination, shaped like plan legs.

    The planner only returns the best journeys, and drops a direct service whenever
    another way is faster: Opéra → République on metro line 8 comes back as "ride to
    Strasbourg–Saint-Denis and walk", so it never counts as direct. The board lists
    every departure regardless, with where each one goes next."""
    board = _fetch(
        MOTIS_BOARD_URL,
        {
            "stopId": stop_id,
            "time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "n": BOARD_DEPARTURES,
            "mode": ",".join(sorted(modes)),
            "fetchStops": "true",
        },
    )
    legs = []
    for dep in board.get("stopTimes", []):
        following = dep.get("nextStops") or []
        # The following stop nearest the destination, not the first one within reach:
        # line 3 passes Arts et Métiers (550 m from République) before République.
        near = [
            (_distance_m(stop["lat"], stop["lon"], *destination), i)
            for i, stop in enumerate(following)
            if stop.get("dropoffType") != "NOT_ALLOWED"
        ]
        near = [(d, i) for d, i in near if d <= _reach(modes)]
        arrival_at = min(near)[1] if near else None
        if arrival_at is None or dep.get("mode") not in modes:
            continue
        frm, to = dep["place"], following[arrival_at]
        legs.append(
            {
                **{k: dep.get(k) for k in (
                    "mode", "headsign", "displayName", "routeShortName", "tripShortName",
                    "agencyName", "routeColor", "realTime", "tripId", "tripTo",
                )},
                "cancelled": dep.get("cancelled") or dep.get("tripCancelled"),
                "from": frm,
                "to": to,
                "intermediateStops": following[:arrival_at],
                "scheduledStartTime": frm.get("scheduledDeparture"),
                "startTime": frm.get("departure"),
                "scheduledEndTime": to.get("scheduledArrival"),
                "endTime": to.get("arrival"),
            }
        )
    return legs


def _stop_places(lat, lng, modes):
    """IDs of the timetable stops near a point served by one of `modes`, nearest first.

    Searching from a point relies on MOTIS walking to a stop, and some stops are cut
    off from its footpath network: the SNCF stop at Paris Austerlitz, 160 m from the
    station, is only "reachable" by bus, so nothing direct is found from the station.
    Searching from the stop itself does not need that walk."""
    stops = [
        stop
        for stop in _fetch(
            MOTIS_STOPS_URL,
            {"place": f"{lat:.6f},{lng:.6f}", "type": "STOP"},
            ttl=_STOPS_CACHE_TTL,
            queued=False,
        )
        if stop.get("id")
        and modes & set(stop.get("modes") or ())
        and _distance_m(stop["lat"], stop["lon"], lat, lng) <= _reach(modes)
    ]
    stops.sort(key=lambda stop: _distance_m(stop["lat"], stop["lon"], lat, lng))
    return [stop["id"] for stop in stops[:MAX_FALLBACK_STOPS]]


# A stop is moved onto the run's path only when this close: further than that, the path is
# more likely truncated or wrong than the stop.
MAX_SNAP_M = 150


def _leg_path(leg):
    """The run's path as [(lat, lng), ...], from MOTIS's legGeometry, or None.

    Only planner legs have one (departure-board runs don't)."""
    geometry = leg.get("legGeometry") or {}
    if not geometry.get("points"):
        return None
    try:
        return polyline.decode(geometry["points"], precision=geometry.get("precision", 6))
    except (ValueError, IndexError):
        return None


def _pattern(departure):
    """What makes runs share a path: same agency and line, calling at the same stops."""
    return (departure["agency"], departure["line"], tuple(stop[2] for stop in departure["stops"]))


def _snap_stops(stops, path):
    """Move each intermediate stop onto the nearest point of the run's path.

    The stops become the trip's via waypoints, and a stop's coordinates can be well off
    the track it is served on: IDFM puts the RER A's Gare de Lyon on top of the surface
    tracks of the terminus, 64 m from the tunnel the RER A actually runs in, so the router
    detoured into the terminus and reversed out to reach it. The path follows the line."""
    if not path or len(path) < 2:
        return
    for stop in stops:
        lat, lng = stop[0], stop[1]
        k = math.cos(math.radians(lat)) * 111320
        best = None
        for (alat, alng), (blat, blng) in zip(path, path[1:]):
            ax, ay, bx, by = alng * k, alat * 110540, blng * k, blat * 110540
            px, py = lng * k, lat * 110540
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ((dx * dx + dy * dy) or 1)))
            x, y = ax + t * dx, ay + t * dy
            dist = math.hypot(px - x, py - y)
            if best is None or dist < best[0]:
                best = (dist, y / 110540, x / k)
        if best and best[0] <= MAX_SNAP_M:
            stop[0], stop[1] = round(best[1], COORD_DIGITS), round(best[2], COORD_DIGITS)


def _headsign(leg):
    """Where the run is going. IDFM puts the RER/Transilien mission code (NATO, QIWI) in
    the headsign; the terminus name is in tripTo."""
    headsign = (leg.get("headsign") or "").strip()
    terminus = ((leg.get("tripTo") or {}).get("name") or "").strip()
    if terminus and (not headsign or re.fullmatch(r"[A-Z]{4}", headsign)):
        return terminus
    return headsign


def _hex_colour(value):
    return f"#{value}" if value and re.fullmatch(r"[0-9A-Fa-f]{6}", value) else None


def _departure_utc(leg):
    return _parse_utc(leg.get("scheduledStartTime") or leg.get("startTime"))


def _duration_s(leg):
    dep = _departure_utc(leg)
    arr = _parse_utc(leg.get("scheduledEndTime") or leg.get("endTime"))
    return int((arr - dep).total_seconds()) if dep and arr else None


def _delay_minutes(actual, scheduled):
    a, s = _parse_utc(actual), _parse_utc(scheduled)
    return round((a - s).total_seconds() / 60) if a and s else 0


def _search(args):
    """The search the form asks for (from, to, date, time, type), as
    (trip type, origin, destination, start, window, plan parameters, modes)."""
    trip_type = args.get("type", "train")
    if trip_type not in TRANSIT_MODES:
        abort(400)
    origin = _parse_coords(args.get("from"))
    destination = _parse_coords(args.get("to"))
    try:
        day = datetime.strptime(args.get("date") or "", "%Y-%m-%d")
    except ValueError:
        abort(400)
    try:
        clock = datetime.strptime(args.get("time") or "", "%H:%M").time()
        at = day.replace(hour=clock.hour, minute=clock.minute)
    except ValueError:
        clock, at = None, day
    page = args.get("page")
    if page == "later":
        # "Later" in the list: from its last departure (date/time), as far ahead as usual.
        start, window = at, WINDOW_AHEAD
    elif page == "earlier":
        # "Earlier": the stretch just before its first departure (date/time), as long as
        # the list already covers (span, minutes): a few minutes on a busy line, hours
        # on a sparse one.
        try:
            span = timedelta(minutes=min(max(int(args.get("span") or 60), 10), 24 * 60))
        except ValueError:
            abort(400)
        start, window = at - span, span - timedelta(minutes=1)
    elif clock:
        start, window = at - WINDOW_BEFORE, WINDOW_LENGTH
        start = start.replace(minute=start.minute - start.minute % (START_STEP.seconds // 60))
    else:
        # No time on the form: from the start of the day.
        start, window = day, WINDOW_AHEAD
    start = start.replace(tzinfo=_tz(*origin)).astimezone(timezone.utc)

    params = {
        "fromPlace": f"{origin[0]:.6f},{origin[1]:.6f}",
        "toPlace": f"{destination[0]:.6f},{destination[1]:.6f}",
        "time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "searchWindow": int(min(window, SEARCH_WINDOW).total_seconds()),
        "maxTransfers": 0,
        "transitModes": TRANSIT_MODES[trip_type],
        "directModes": "WALK",
        "numItineraries": ITINERARIES,
        "maxItineraries": ITINERARIES,
        "timetableView": "true",
        "detailedTransfers": "false",
        # Keep direct services a faster one overtakes (an L1 stopping train behind an
        # RE11): Pareto-optimal results drop them, but people do take them.
        "slowDirect": "true",
    }
    modes = set(TRANSIT_MODES[trip_type].split(","))
    return trip_type, origin, destination, start, window, params, modes


def _get(url, params):
    return requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)


def _fetch(url, params, ttl=_CACHE_TTL, queued=True):
    """A Transitous answer, from the cache if asked recently. Queued requests share
    TRANSITOUS_PARALLEL slots; a 429 is retried once, then starts the backoff."""
    global _backoff_until
    key = (url,) + tuple(sorted(params.items()))
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    cancel = _cancel.get()
    for attempt in range(2):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if time.monotonic() < _backoff_until:
            raise RateLimited("Transitous rate limit: backing off")
        if queued:
            with _transitous_slots:
                # Waiting for a slot can outlast the request it is for.
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                response = _get(url, params)
        else:
            response = _get(url, params)
        if response.status_code != 429:
            break
        if attempt == 0:
            time.sleep(RETRY_AFTER_429_S)
    if response.status_code == 429:
        try:
            wait = max(1, int(response.headers.get("Retry-After", "")))
        except ValueError:
            wait = RATE_LIMIT_BACKOFF_S
        _backoff_until = time.monotonic() + wait
        logger.warning("Transitous rate limit (429): no requests for %d s", wait)
        raise RateLimited("Transitous rate limit (429)")
    if response.status_code == 400 and _note_timetable_window(response):
        raise OutsideTimetable(response.json().get("error"))
    response.raise_for_status()
    data = response.json()
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = (time.monotonic() + ttl, data)
    return data


def _client_gone(sock):
    """Whether the browser closed the request's connection (gunicorn's socket for it;
    None elsewhere, e.g. under the Flask dev server, where nothing is cancelled)."""
    if sock is None:
        return False
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        # Readable with nothing to read: the other end has closed.
        return bool(readable) and sock.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


def _quietly(search, *args):
    """A secondary search that fails (MOTIS rejects a stop, times out) finds nothing,
    rather than failing the ones that worked."""
    try:
        return search(*args)
    except (RateLimited, Cancelled, OutsideTimetable):
        return []  # a 429 is logged once, when it came in
    except (requests.RequestException, ValueError) as e:
        logger.info("MOTIS secondary search failed: %s", e)
        return []


@motis_blueprint.route("/u/<username>/motis/departures")
@login_required
def motis_departures(username):
    return _departures_for(request.args)


def _departures_for(args):
    """The departures list for a search (from, to, date, time, type, page…), as the
    JSON response the forms get."""
    _, origin, destination, start, window, params, modes = _search(args)
    if _outside_timetable(start, window):
        return jsonify({"departures": [], "outside_timetable": True})

    def direct_legs(from_place, to_place):
        plan = _fetch(MOTIS_PLAN_URL, dict(params, fromPlace=from_place, toPlace=to_place))
        return [
            leg
            for it in plan.get("itineraries", [])
            if (leg := _direct_leg(it, modes, origin, destination))
        ]

    points = (params["fromPlace"], params["toPlace"])
    client = request.environ.get("gunicorn.socket")
    cancel = threading.Event()
    pool = ThreadPoolExecutor(max_workers=4)

    def submit(fn, *args):
        # Each task in its own copy of this context, which carries the cancel flag.
        ctx = contextvars.copy_context()
        ctx.run(_cancel.set, cancel)
        return pool.submit(ctx.run, fn, *args)

    def result(future):
        # Wait for a task, giving up as soon as the browser has.
        while True:
            try:
                return future.result(timeout=CANCEL_POLL_S)
            except FutureTimeout:
                if _client_gone(client):
                    cancel.set()
                    raise Cancelled()

    try:
        # From the points, and at the same time look up the stops near them.
        from_points = submit(direct_legs, *points)
        from_stops = submit(_quietly, _stop_places, *origin, modes)
        to_stops = submit(_quietly, _stop_places, *destination, modes)
        from_stops, to_stops = result(from_stops), result(to_stops)
        # Always also from the nearest stop at each end: walking from the point can
        # reach only some of a big station's platforms (Oslo S: RE11 and L1 are
        # missing from the point search, there from the stop's).
        nearest = (from_stops[0] if from_stops else points[0], to_stops[0] if to_stops else points[1])
        from_nearest = submit(_quietly, direct_legs, *nearest) if nearest != points else None
        board = (
            submit(_quietly, _board_legs, from_stops[0], modes, start, destination)
            if from_stops else None
        )
        searches = [result(f) for f in (from_points, from_nearest, board) if f]
        if not any(searches):
            # Still nothing: every remaining point/stop combination (the nearest
            # stop can be the wrong feed's, e.g. the RER one at Paris Austerlitz).
            pairs = [
                (f, t)
                for f in [points[0], *from_stops]
                for t in [points[1], *to_stops]
                if (f, t) not in (points, nearest)
            ]
            searches = [result(f) for f in [submit(_quietly, direct_legs, *pair) for pair in pairs]]
    except OutsideTimetable:
        # A date outside the loaded timetables: nothing to look up, not a failure.
        return jsonify({"departures": [], "outside_timetable": True})
    except Cancelled:
        # Nobody is waiting for this answer any more.
        return "", 204
    except RateLimited:
        return jsonify({"error": "rate_limited", "departures": []}), 503
    except (requests.RequestException, ValueError) as e:
        logger.warning("MOTIS request failed: %s", e)
        return jsonify({"error": "unavailable", "departures": []}), 502
    finally:
        # Don't wait for requests already sent: they finish in the background, and
        # whatever hasn't started yet is dropped.
        pool.shutdown(wait=False, cancel_futures=True)

    return jsonify({"departures": _departures(searches, start, window, destination)})


def _departures(searches, start, window, destination):
    """The form's departure list from the direct legs each search found."""
    legs = [leg for found in searches for leg in found]
    # Each search widened its window until it had ITINERARIES results, so one that
    # reaches few departures (walking from the point misses most of Oslo S's platforms)
    # runs days ahead while a denser one stops after a few hours. Cut the merged list
    # where the densest full search ends, so it doesn't trail off into stray runs from
    # days later, but not before MIN_HORIZON: a line with a few runs a day still
    # shows the next day's. Searches that came back short ran out of range, not results.
    horizons = [
        max(_departure_utc(leg) for leg in found)
        for found in searches
        if len(found) >= ITINERARIES // 2
    ]
    end = start + window
    if horizons:
        end = min(end, max(min(horizons), start + WINDOW_BEFORE + MIN_HORIZON))
    departures, seen = [], {}
    for leg in legs:
        if not leg or leg.get("cancelled"):
            continue
        dep_utc = _departure_utc(leg)
        if not dep_utc or not (start <= dep_utc <= end):
            continue
        # The same run comes back once per walking variant to it, and once per feed
        # when two feeds both publish it (SNCF and Trenitalia both carry Frecciarossa):
        # one entry per agency departing and arriving at the same moment.
        # One entry per run: the same train comes back from several searches (and,
        # Frecciarossa-style, from two feeds), sometimes alighting at different stops
        # of the destination station (Temple vs République).
        # The line's first word too: RER A and RER E share the agency name "RER" and can
        # leave the same minute; the SNCF and Trenitalia copies of a Frecciarossa still
        # agree on it ("FR 6441" / "FR").
        trip_key = (
            (leg.get("agencyName") or "").strip().lower(),
            leg.get("scheduledStartTime"),
            ((leg.get("displayName") or leg.get("routeShortName") or "").split() or [""])[0].lower(),
        )
        stops = leg.get("intermediateStops", [])

        frm, to = leg["from"], leg["to"]
        from_tz = ZoneInfo(frm["tz"]) if frm.get("tz") else _tz(frm["lat"], frm["lon"])
        to_tz = ZoneInfo(to["tz"]) if to.get("tz") else _tz(to["lat"], to["lon"])
        entry = {
            "departure": _local(leg.get("scheduledStartTime") or leg.get("startTime"), from_tz),
            "arrival": _local(leg.get("scheduledEndTime") or leg.get("endTime"), to_tz),
            # Scheduled, in seconds: the two local times above can be in different zones.
            "duration": _duration_s(leg),
            "departure_delay": _delay_minutes(leg.get("startTime"), leg.get("scheduledStartTime")) if leg.get("realTime") else 0,
            "arrival_delay": _delay_minutes(leg.get("endTime"), leg.get("scheduledEndTime")) if leg.get("realTime") else 0,
            "line": _line_name(leg),
            "headsign": _headsign(leg),
            "agency": (leg.get("agencyName") or "").strip(),
            "mode": leg.get("mode"),
            # Transitous's id of the run, to fetch its actual times once it has run
            # (while the feed still has them): kept with each saved stop (stopRecord).
            "trip_id": leg.get("tripId"),
            "color": _hex_colour(leg.get("routeColor")),
            "from_name": frm.get("name"),
            "to_name": to.get("name"),
            # Where it leaves from and arrives (the live platform if it was changed): the
            # trip's departure_platform / arrival_platform when picked.
            "from_platform": (frm.get("track") or frm.get("scheduledTrack") or "").strip() or None,
            "to_platform": (to.get("track") or to.get("scheduledTrack") or "").strip() or None,
            "stops": [_stop(s) for s in stops],
            "_sort": dep_utc,
            "_path": _leg_path(leg),
        }
        entry["_dest_m"] = _distance_m(to["lat"], to["lon"], *destination)
        kept = seen.get(trip_key)
        if kept:
            # Keep the arrival at the stop nearest the destination...
            if entry["_dest_m"] < kept["_dest_m"] - 50:
                for field in ("arrival", "arrival_delay", "duration", "to_name", "to_platform", "stops", "_dest_m"):
                    kept[field] = entry[field]
            # ...and, for the rest, whatever each copy has that the other lacks (one
            # feed the train number, the other the headsign).
            if not kept["_path"]:
                kept["_path"] = entry["_path"]
            for field in ("line", "headsign", "color"):
                if len(entry[field] or "") > len(kept[field] or ""):
                    kept[field] = entry[field]
            continue
        seen[trip_key] = entry
        departures.append(entry)

    departures.sort(key=lambda d: d.pop("_sort"))
    del departures[MAX_DEPARTURES:]
    # Departure-board runs come without a path. Runs of the same line calling at the
    # same stops follow the same route, so they borrow one from the planner's runs:
    # unsnapped, a Gyldenpris (Bergen) stop 6 m off the road sent the router into the
    # tunnel underneath it.
    patterns = {}
    for d in departures:
        if d["_path"]:
            patterns.setdefault(_pattern(d), d["_path"])
    for d in departures:
        if not d["_path"]:
            d["_path"] = patterns.get(_pattern(d))
    for d in departures:
        d.pop("_dest_m")
        path = d.pop("_path")
        _snap_stops(d["stops"], path)

    if departures:
        try:
            operators = _resolve_operators(
                {d["agency"] for d in departures if d["agency"]}, start.date()
            )
        except Exception:
            logger.exception("Operator lookup for MOTIS agencies failed")
            operators = {}
        for d in departures:
            info = operators.get(d["agency"], {})
            d["operator"] = info.get("operator") or d["agency"]
            d["logo_url"] = info.get("logo_url")

    return departures


# Live times are what this is for, so not the plan's few minutes of cache.
_TRIP_CACHE_TTL = 30


def _run_stops(trip_id):
    """A run's stops, first to last, scheduled and live, and whether Transitous has live
    data for it. Raises what _fetch raises; an id it doesn't know (left the feed) is
    no stops."""
    try:
        trip = _fetch(MOTIS_TRIP_URL, {"tripId": trip_id}, ttl=_TRIP_CACHE_TTL)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (400, 404, 422):
            return [], False
        raise
    legs = [leg for leg in trip.get("legs") or [] if leg.get("mode") != "WALK"]
    if not legs:
        return [], False
    leg = legs[0]
    stops = []
    for place in [leg.get("from") or {}, *(leg.get("intermediateStops") or []), leg.get("to") or {}]:
        if place.get("lat") is None:
            continue
        stops.append({
            "id": place.get("stopId"),
            "name": place.get("name"),
            "lat": round(place["lat"], COORD_DIGITS),
            "lng": round(place["lon"], COORD_DIGITS),
            "arr": _utc(place.get("scheduledArrival")),
            "dep": _utc(place.get("scheduledDeparture")),
            "arr_rt": _live(place, "Arrival"),
            "dep_rt": _live(place, "Departure"),
            "platform": (place.get("scheduledTrack") or place.get("track") or "").strip() or None,
            "platform_rt": (place.get("track") or "").strip() or None,
            "tz": place.get("tz"),
            "cc": getCountryFromCoordinates(place["lat"], place["lon"])["countryCode"],
        })
    return stops, bool(leg.get("realTime"))


@motis_blueprint.route("/u/<username>/motis/trip")
@login_required
def motis_trip(username):
    """A run's stops with their current times, by the Transitous id saved with a trip's
    stops (stopRecord in new.html): what the stops dialog of the edit page refreshes the
    live times and platforms from, while Transitous still has the run (usually not long
    after it ends). Every stop of the run, first to last, scheduled and live."""
    trip_id = (request.args.get("id") or "").strip()
    if not trip_id or len(trip_id) > 300:
        abort(400)
    try:
        stops, realtime = _run_stops(trip_id)
    except RateLimited:
        return jsonify({"error": "rate_limited", "stops": []}), 503
    except OutsideTimetable:
        return jsonify({"stops": []})
    except (requests.RequestException, ValueError) as e:
        logger.warning("MOTIS trip request failed: %s", e)
        return jsonify({"error": "unavailable", "stops": []}), 502
    return jsonify({"stops": stops, "realtime": realtime})


def trip_runs(waypoints_json):
    """The Transitous runs a trip's stops were picked from, when every stop says which
    (a stop typed in by hand has none: its times can't be refreshed, so neither can the
    trip's); else []."""
    try:
        waypoints = json.loads(waypoints_json or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(waypoints, list):
        return []
    stops = [wp["stop"] for wp in waypoints if isinstance(wp, dict) and isinstance(wp.get("stop"), dict)]
    if not stops or not all(st.get("trip") for st in stops):
        return []
    return list(dict.fromkeys(st["trip"] for st in stops))


def _seconds_between(actual, scheduled):
    if not actual or not scheduled:
        return None
    return int((_parse_utc(actual) - _parse_utc(scheduled)).total_seconds())


def _live_update(trip_row, path):
    """What the runs' live data says now about a trip in progress: its delays at its two
    ends (the runs' stops nearest them), platforms there, and its stops' live times.
    None when there is nothing live to go by."""
    runs = trip_runs(trip_row["waypoints"])
    if not runs or len(path) < 2:
        return None
    run_stops, live = [], False
    by_id = {}
    for run in runs:
        stops, realtime = _run_stops(run)
        live = live or realtime
        run_stops.extend(stops)
        by_id.update({(run, st["id"]): st for st in stops if st.get("id")})
    if not live or not run_stops:
        return None

    def nearest(point):
        found = min(
            ((_distance_m(st["lat"], st["lng"], *point), i) for i, st in enumerate(run_stops)),
            default=None,
        )
        return run_stops[found[1]] if found and found[0] <= 1500 else None

    start, end = nearest(path[0]), nearest(path[-1])
    if not start and not end:
        return None
    waypoints = json.loads(trip_row["waypoints"])
    for wp in waypoints:
        stop = wp.get("stop") if isinstance(wp, dict) else None
        fresh = isinstance(stop, dict) and by_id.get((stop.get("trip"), stop.get("id")))
        if not fresh:
            continue
        for field in ("arr", "dep"):
            if fresh[field]:
                stop[field] = fresh[field]
            if fresh[field + "_rt"]:
                stop[field + "_rt"] = fresh[field + "_rt"]
            else:
                stop.pop(field + "_rt", None)
        if fresh["platform"]:
            stop["platform"] = fresh["platform"]
        if fresh["platform_rt"] and fresh["platform_rt"] != stop.get("platform"):
            stop["platform_rt"] = fresh["platform_rt"]
        else:
            stop.pop("platform_rt", None)

    def platform(st):
        return st and (st["platform_rt"] or st["platform"])

    dep = start and _seconds_between(start["dep_rt"], start["dep"])
    arr = end and _seconds_between(end["arr_rt"], end["arr"])
    return {
        "departure_delay": dep if dep is not None else trip_row["departure_delay"],
        "arrival_delay": arr if arr is not None else trip_row["arrival_delay"],
        "departure_platform": platform(start) or trip_row["departure_platform"],
        "arrival_platform": platform(end) or trip_row["arrival_platform"],
        "waypoints": json.dumps(waypoints, ensure_ascii=False),
    }


@motis_blueprint.route("/u/<username>/current/live", methods=["GET", "POST"])
@login_required
def current_trip_live(username):
    """The trip in progress against its runs' live data (GTFS-RT through Transitous):
    GET says whether there is any, and the delays it gives; POST also saves them (and
    the stops' live times and the platforms) to the trip. The dashboard's refresh."""
    from src.trips.utils import get_current_trip_id  # noqa: PLC0415 (import cycle)

    trip_id = get_current_trip_id(username)
    if trip_id is None:
        return jsonify({"available": False})
    with pg_session() as pg:
        row = pg.execute(
            """SELECT t.waypoints, t.departure_delay, t.arrival_delay,
                      t.departure_platform, t.arrival_platform,
                      (SELECT json_agg(json_build_array(ST_Y(dp.geom), ST_X(dp.geom)) ORDER BY dp.path)
                       FROM paths p, ST_DumpPoints(p.geom) AS dp WHERE p.trip_id = t.trip_id) AS path
               FROM trips t WHERE t.trip_id = :trip_id""",
            {"trip_id": trip_id},
        ).fetchone()
    if row is None:
        return jsonify({"available": False})
    row = row._mapping
    try:
        update = _live_update(row, row["path"] or [])
    except RateLimited:
        return jsonify({"available": False, "error": "rate_limited"}), 503
    except (requests.RequestException, ValueError) as e:
        logger.warning("MOTIS live refresh failed: %s", e)
        return jsonify({"available": False, "error": "unavailable"}), 502
    if update is None:
        return jsonify({"available": False})
    if request.method == "POST":
        with pg_session() as pg:
            pg.execute(
                """UPDATE trips SET departure_delay = :departure_delay, arrival_delay = :arrival_delay,
                          departure_platform = :departure_platform, arrival_platform = :arrival_platform,
                          waypoints = :waypoints, last_modified = :last_modified
                   WHERE trip_id = :trip_id""",
                {**update, "trip_id": trip_id, "last_modified": datetime.now()},
            )
    return jsonify({
        "available": True,
        "saved": request.method == "POST",
        "departure_delay": update["departure_delay"],
        "arrival_delay": update["arrival_delay"],
        "waypoints": update["waypoints"],
    })
