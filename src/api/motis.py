"""Timetable suggestions for the new-trip form, from MOTIS (the Transitous instance).

Given the origin and destination the user picked, list the *direct* services between
them around the chosen departure time, already shaped for the form: local times,
the line, the intermediate stops (which become the trip's via waypoints) and the
operator resolved to a Trainlog operator so its logo can be shown.
"""

import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
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
USER_AGENT = "Trainlog/1.0 (https://trainlog.me; admin@trainlog.me; trip form timetable suggestions)"

# Search from a little before the time on the form to a full day after it: the form
# time is often just "now" or a rough guess, so the next day's runs are offered too.
WINDOW_BEFORE = timedelta(hours=1)
WINDOW_LENGTH = WINDOW_BEFORE + timedelta(hours=24)
# Ask for a number of results rather than a fixed window: MOTIS starts with a short
# window and widens it until it has found this many itineraries. A fixed 25 h window
# gave anything from 1 to 450 on sparse vs busy lines. Each departure tends to come
# back about twice (walking variants, slowDirect copies, merged below), so this is
# ~20 departures. WINDOW_LENGTH still caps how far ahead results are kept.
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
_CACHE_MAX = 256
_cache = {}

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


def _stop(stop):
    """[lat, lng, name, country code, arrival 'HH:MM', departure 'HH:MM'] — the
    country so the form can flag it like a picked station, the times as shown there."""
    tz = ZoneInfo(stop["tz"]) if stop.get("tz") else _tz(stop["lat"], stop["lon"])

    def clock(field):
        value = _local(stop.get("scheduled" + field) or stop.get(field.lower()), tz)
        return value[11:] if value else None

    return [
        stop["lat"],
        stop["lon"],
        stop.get("name", ""),
        getCountryFromCoordinates(stop["lat"], stop["lon"])["countryCode"],
        clock("Arrival"),
        clock("Departure"),
    ]


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
        for stop in _fetch(MOTIS_STOPS_URL, {"place": f"{lat:.6f},{lng:.6f}", "type": "STOP"})
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


# How far along the run's path its routing ends are taken: off the stop point, still
# within the platform.
TRACK_END_M = 100


def _along(path, metres):
    """The first point of the path at least this far along it, or its last point."""
    walked = 0.0
    for a, b in zip(path, path[1:]):
        walked += _distance_m(*a, *b)
        if walked >= metres:
            return list(b)
    return list(path[-1])


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
            stop[0], stop[1] = best[1], best[2]


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


def _fetch(url, params):
    key = (url,) + tuple(sorted(params.items()))
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    response.raise_for_status()
    data = response.json()
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = (time.monotonic() + _CACHE_TTL, data)
    return data


def _quietly(search, *args):
    """A secondary search that fails (MOTIS rejects a stop, times out) finds nothing,
    rather than failing the ones that worked."""
    try:
        return search(*args)
    except (requests.RequestException, ValueError) as e:
        logger.info("MOTIS secondary search failed: %s", e)
        return []


@motis_blueprint.route("/u/<username>/motis/departures")
@login_required
def motis_departures(username):
    trip_type = request.args.get("type", "train")
    if trip_type not in TRANSIT_MODES:
        return jsonify({"departures": []})

    origin = _parse_coords(request.args.get("from"))
    destination = _parse_coords(request.args.get("to"))
    origin_tz = _tz(*origin)
    try:
        day = datetime.strptime(request.args.get("date", ""), "%Y-%m-%d")
    except ValueError:
        abort(400)
    try:
        clock = datetime.strptime(request.args.get("time", ""), "%H:%M").time()
        start, window = day.replace(hour=clock.hour, minute=clock.minute) - WINDOW_BEFORE, WINDOW_LENGTH
    except ValueError:
        # No time on the form: the whole day.
        start, window = day, timedelta(days=1)
    start = start.replace(tzinfo=origin_tz).astimezone(timezone.utc)

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
    def direct_legs(from_place, to_place):
        plan = _fetch(MOTIS_PLAN_URL, dict(params, fromPlace=from_place, toPlace=to_place))
        return [
            leg
            for it in plan.get("itineraries", [])
            if (leg := _direct_leg(it, modes, origin, destination))
        ]

    points = (params["fromPlace"], params["toPlace"])
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            # From the points, and at the same time look up the stops near them.
            from_points = pool.submit(direct_legs, *points)
            from_stops = pool.submit(_quietly, _stop_places, *origin, modes)
            to_stops = pool.submit(_quietly, _stop_places, *destination, modes)
            from_stops, to_stops = from_stops.result(), to_stops.result()
            # Always also from the nearest stop at each end: walking from the point can
            # reach only some of a big station's platforms (Oslo S: RE11 and L1 are
            # missing from the point search, there from the stop's).
            nearest = (from_stops[0] if from_stops else points[0], to_stops[0] if to_stops else points[1])
            board = (
                pool.submit(_quietly, _board_legs, from_stops[0], modes, start, destination)
                if from_stops else None
            )
            searches = [from_points.result()]
            if nearest != points:
                searches.append(_quietly(direct_legs, *nearest))
            if board:
                searches.append(board.result())
            legs = [leg for found in searches for leg in found]
            if not legs:
                # Still nothing: every remaining point/stop combination (the nearest
                # stop can be the wrong feed's, e.g. the RER one at Paris Austerlitz).
                pairs = [
                    (f, t)
                    for f in [points[0], *from_stops]
                    for t in [points[1], *to_stops]
                    if (f, t) not in (points, nearest)
                ]
                searches = list(pool.map(lambda pair: _quietly(direct_legs, *pair), pairs))
                legs = [leg for found in searches for leg in found]
    except (requests.RequestException, ValueError) as e:
        logger.warning("MOTIS request failed: %s", e)
        return jsonify({"error": "unavailable", "departures": []}), 502

    # Each search widened its window until it had ITINERARIES results, so one that
    # reaches few departures (walking from the point misses most of Oslo S's platforms)
    # runs days ahead while a denser one stops after a few hours. Cut the merged list
    # where the densest full search ends, so it doesn't trail off into stray runs from
    # the next day. Searches that came back short ran out of range, not results.
    horizons = [
        max(_departure_utc(leg) for leg in found)
        for found in searches
        if len(found) >= ITINERARIES // 2
    ]
    end = start + window
    if horizons:
        end = min(end, min(horizons))
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
            "color": _hex_colour(leg.get("routeColor")),
            "from_name": frm.get("name"),
            "to_name": to.get("name"),
            "stops": [_stop(s) for s in stops],
            "_sort": dep_utc,
            "_path": _leg_path(leg),
        }
        entry["_dest_m"] = _distance_m(to["lat"], to["lon"], *destination)
        kept = seen.get(trip_key)
        if kept:
            # Keep the arrival at the stop nearest the destination...
            if entry["_dest_m"] < kept["_dest_m"] - 50:
                for field in ("arrival", "arrival_delay", "duration", "to_name", "stops", "_dest_m"):
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
    for d in departures:
        d.pop("_dest_m")
        path = d.pop("_path")
        _snap_stops(d["stops"], path)
        # Where the run leaves and arrives on its track. The router starts from the
        # nearest track to a point, and a station's point can be nearer another line's
        # (La Défense's sits by the Transilien L, which the RER A route then looped round).
        # The path itself starts at that same stop point, so step a little along it.
        d["track_ends"] = (
            [_along(path, TRACK_END_M), _along(path[::-1], TRACK_END_M)]
            if path and len(path) > 1 else None
        )

    if departures:
        try:
            operators = _resolve_operators(
                {d["agency"] for d in departures if d["agency"]}, day.date()
            )
        except Exception:
            logger.exception("Operator lookup for MOTIS agencies failed")
            operators = {}
        for d in departures:
            info = operators.get(d["agency"], {})
            d["operator"] = info.get("operator") or d["agency"]
            d["logo_url"] = info.get("logo_url")

    return jsonify({"departures": departures})
