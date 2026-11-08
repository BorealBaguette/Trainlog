"""src/api/mcp_form.py — the trip forms, server side, for the MCP server.

The website's trip forms check what is typed in the browser (a currency from the
list, every field of the chosen date precision, a power type the trip type offers, a
route the router found…) and then post a payload the server trusts: new.html and
routing.html post to saveTrip, the edit page to updateTrip/patchTrip, the plan builder
to savePlanTrip and its editor to edit_full. An AI on the MCP has no browser, so this
module is that browser: it checks the MCP arguments the way the forms do, routes the
trip the way the routing page does, and builds the payload the form would post, which
then goes through the site's own save code (app.py) unchanged.

Everything here raises ValueError with a message meant for the AI when an argument is
one the website would not let a user enter.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from py.utils import get_flag_emoji, getCountryFromCoordinates, getDistance
from src.ai import get_airport_by_iata, path_length, route_via
from src.api.motis import TRANSIT_MODES, OutsideTimetable, RateLimited, run_segment
from src.consts import TripTypes
from src.currency import get_available_currencies
from src.paths import geom_geojson_to_coords
from src.pg import pg_session
from src.utils import get_default_trip_visibility, getLocalDatetime, getUtcDatetime


def site():
    """app.py, for the form handlers that live there (saveTripToDb, apply_trip_patch,
    station_autocomplete…). Imported on use: app.py imports the MCP blueprint."""
    import app

    return app


# ── what each trip type's form offers ────────────────────────────────────────────

RAIL_TYPES = ("train", "tram", "metro", "funicular", "rail")
AIR_TYPES = ("air", "helicopter")
# One place, no route (routing.html's SINGLE_POINT): the destination is the origin.
SINGLE_POINT_TYPES = ("accommodation", "restaurant", "poi")
# The power type radios of new.html / edit_copy.html, the first being the one checked.
POWER_TYPES = {
    "train": ("auto", "electric", "thermic"),
    "bus": ("thermic", "electric"),
    "car": ("thermic", "electric"),
    "cycle": ("manual", "electric"),
    "scooter": ("manual", "electric"),
    "ferry": ("thermic", "electric"),
}
# The trainset builder button, and the ferry's CO2 override field.
TRAINSET_TYPES = ("train", "metro", "tram")
CO2_OVERRIDE_TYPES = ("ferry",)
VISIBILITIES = ("public", "friends", "private")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# Leading flag emoji of a station label ("🇫🇷 Paris Gare de Lyon").
FLAG_RE = re.compile(r"^[\U0001F1E6-\U0001F1FF]{2}\s*")


def form_types():
    """The trip types the new-trip form can make (get_new_trip_types: all but 'other')."""
    return [t for group in site().TRIP_TYPE_GROUPS for t in group if t != "other"]


def check_type(trip_type):
    if trip_type not in form_types():
        raise ValueError(f"Unknown trip type {trip_type!r}; use one of {form_types()}.")
    return trip_type


def strip_flag(name):
    return FLAG_RE.sub("", name or "").strip()


def flagged(name, lat, lng):
    """A station label as the site saves it: its country's flag, then the name. The
    flag comes from where the place is (saveTripToDb's rule for a typed station), so a
    label never goes without one, nor with another country's."""
    code = getCountryFromCoordinates(lat, lng)["countryCode"]
    return f"{get_flag_emoji(code)} {strip_flag(name)}"


# ── small argument checks ────────────────────────────────────────────────────────


def text(args, key, max_len=1000):
    """An optional free-text argument: None when absent or blank."""
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"{key} must be text.")
    value = str(value).strip()
    if len(value) > max_len:
        raise ValueError(f"{key} is longer than {max_len} characters.")
    return value or None


def number(args, key, minimum=None):
    value = args.get(key)
    if value in (None, ""):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number.")
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError(f"{key} must be a number of at least {minimum}." if minimum is not None
                         else f"{key} must be a finite number.")
    return value


def integer(args, key, minimum=None):
    value = number(args, key, minimum)
    if value is None:
        return None
    if value != int(value):
        raise ValueError(f"{key} must be a whole number.")
    return int(value)


def iso_date(value, key):
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a date YYYY-MM-DD.")


def local_datetime(value, key):
    """'YYYY-MM-DDTHH:MM' (or with a space) as the form's datetime fields hold it."""
    value = str(value or "").strip().replace(" ", "T")[:16]
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError(f"{key} must be a local date and time YYYY-MM-DDTHH:MM.")


def clock(value, key):
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").strftime("%H:%M")
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a time HH:MM.")


def coordinate(place, role):
    try:
        lat, lng = float(place["lat"]), float(place["lng"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{role} needs numeric lat and lng.")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        raise ValueError(f"{role} has impossible coordinates ({lat}, {lng}).")
    return lat, lng


# ── places and route ─────────────────────────────────────────────────────────────


def resolve_place(place, trip_type, role):
    """An origin/destination/via argument as the form keeps a picked station:
    {label, lat, lng, key}. Airports come from the airport database by IATA code, as
    the air form's airport search gives them; any other place is the name and
    coordinates search_stations returned (or the user's own, like a manual station)."""
    if not isinstance(place, dict):
        raise ValueError(f"{role} must be an object {{name, lat, lng}} (from search_stations).")
    iata = (place.get("iata") or "").strip().upper()
    if iata and trip_type in AIR_TYPES:
        airport = get_airport_by_iata(iata)
        if not airport:
            raise ValueError(f"{role}: no airport with IATA code {iata}. Use search_stations with type 'air'.")
        # The air form's label: `${flag} ${airport.name} (${airport.iata})`.
        return {
            "label": f"{get_flag_emoji(airport['iso_country'])} {airport['name']} ({iata})",
            "lat": float(airport["latitude"]),
            "lng": float(airport["longitude"]),
            "key": None,
        }
    if trip_type == "air":
        raise ValueError(f"{role}: flights go between airports; give its `iata` (from search_stations).")
    name = strip_flag(text(place, "name", 300) or "")
    if not name:
        raise ValueError(f"{role} needs a name.")
    lat, lng = coordinate(place, role)
    key = text(place, "station_key", 300)
    return {"label": flagged(name, lat, lng), "lat": lat, "lng": lng, "key": key}


def resolve_vias(vias, trip_type):
    """Intermediate places in the routing page's saved-waypoint shape
    ({lat, lng, name, hard?}); `exact` makes one a hard waypoint (rail only)."""
    if vias in (None, []):
        return []
    if not isinstance(vias, list):
        raise ValueError("vias must be a list of places.")
    if trip_type in AIR_TYPES or trip_type in SINGLE_POINT_TYPES:
        raise ValueError(f"A {trip_type} trip has no intermediate stops.")
    if len(vias) > 100:
        raise ValueError("At most 100 vias.")
    out = []
    for i, via in enumerate(vias):
        place = resolve_place(via, trip_type, f"vias[{i}]")
        saved = {"lat": place["lat"], "lng": place["lng"], "name": place["label"]}
        if via.get("exact"):
            if trip_type not in RAIL_TYPES:
                raise ValueError("Exact vias are for rail trips only (the rail router's hard waypoints).")
            saved["hard"] = True
        out.append(saved)
    return out


# The edit page's duration estimate for a route no router drew (its GPX import without
# timestamps), in km/h; types it doesn't name go at the default.
FALLBACK_SPEEDS = {"train": 80, "bus": 60, "car": 70, "cycle": 20, "walk": 5}
FALLBACK_SPEED = 50

# How far a routed trip may start or end from its places: routers snap a place off their
# network to the nearest point on it, however far (a sea point reaches Portugal by rail).
# The ferry router is asked to snap up to 10 km (forward_routing_core's radiuses).
ROUTE_END_MAX_M = 2000
FERRY_ROUTE_END_MAX_M = 10000

STRAIGHT_LINE_REMARK = (
    "NO ROUTE FOUND: the router couldn't route this trip, so it was saved as STRAIGHT "
    "LINES between its places (a freehand route), with a distance and duration estimated "
    "from them. Tell the user plainly, and that they can redraw the route on the trip's "
    "edit page (or retry with corrected places or vias)."
)


def route(origin, destination, vias, trip_type):
    """The route the routing page would save: {path, trip_length, estimated_trip_duration,
    details, route_source, straight_line}. Air legs are the great circle with
    air_routing.html's duration; places have none. When the router finds nothing, the
    route — or the route ends away from the places, snapped onto a network far off — it
    is the straight lines through the places, saved as a freehand route (what the
    routing page's freehand mode saves, so the edit page reopens it there to be drawn
    properly), with straight_line set so the AI says so."""
    a = {"lat": origin["lat"], "lng": origin["lng"]}
    b = {"lat": destination["lat"], "lng": destination["lng"]}
    out = {"details": None, "route_source": "router", "straight_line": False}
    if trip_type in SINGLE_POINT_TYPES:
        return {**out, "path": [a, dict(a)], "trip_length": 0, "estimated_trip_duration": 0}
    if trip_type in AIR_TYPES:
        length = getDistance(a, b)
        # air_routing.html: newTrip["estimated_trip_duration"] = (trip_length / 235) + 1800
        return {**out, "path": [a, b], "trip_length": length,
                "estimated_trip_duration": length / 235 + 1800}
    points = [a, *({"lat": v["lat"], "lng": v["lng"]} for v in vias), b]
    modes = ["soft", *("hard" if v.get("hard") else "soft" for v in vias), "soft"] \
        if any(v.get("hard") for v in vias) else None
    path, duration, details = route_via(points, trip_type, modes)
    reach = FERRY_ROUTE_END_MAX_M if trip_type == "ferry" else ROUTE_END_MAX_M
    if not path or len(path) < 2 or getDistance(path[0], a) > reach or getDistance(path[-1], b) > reach:
        path, duration, details = points, None, None
        out.update(route_source="freehand", straight_line=True)
    length = path_length(path)
    if not duration or duration <= 0:
        duration = length / FALLBACK_SPEEDS.get(trip_type, FALLBACK_SPEED) * 3.6
    return {**out, "path": path, "trip_length": length, "estimated_trip_duration": duration,
            "details": details}


# ── timing ───────────────────────────────────────────────────────────────────────

TIMING_KEYS = ("start", "end", "date", "unknown", "duration_minutes",
               "start_day", "end_day", "start_time", "end_time", "weekdays")


def timing_form(args, plan=False):
    """The date precision fields of the form (processFormDates' output) from the MCP
    timing arguments, or None when none was given. One mode at a time:

    - precise: `start` and `end` (local YYYY-MM-DDTHH:MM), both required as on the form;
    - date only: `date`, with an optional `duration_minutes`;
    - unknown: `unknown` = 'past' | 'future', with an optional `duration_minutes`;
    - plans only, relative: `start_day`/`end_day` (Day 1 = the plan's anchor), optional
      `start_time`/`end_time` (both or neither), `weekdays` it runs on, and
      `duration_minutes` for an untimed leg.
    """
    given = {k for k in TIMING_KEYS if args.get(k) not in (None, "")}
    if not given:
        return None
    modes = {
        "preciseDates": given & {"start", "end"},
        "onlyDate": given & {"date"},
        "unknown": given & {"unknown"},
        "relative": given & {"start_day", "end_day", "start_time", "end_time", "weekdays"},
    }
    chosen = [m for m, keys in modes.items() if keys]
    if len(chosen) != 1:
        raise ValueError(
            "Give the timing in exactly one way: start+end, or date, or unknown"
            + (", or start_day/end_day (relative)" if plan else "") + "."
        )
    mode = chosen[0]
    if mode == "relative" and not plan:
        raise ValueError("start_day/end_day/start_time/end_time/weekdays are for plan legs only.")

    form = {"precision": mode, "onlyDateDuration": ""}
    duration = integer(args, "duration_minutes", 0)
    if duration is not None:
        if mode == "preciseDates" or (mode == "relative" and args.get("start_time")):
            raise ValueError("duration_minutes is for legs without times (the duration is the times').")
        form["onlyDateDuration"] = duration * 60

    if mode == "preciseDates":
        if not args.get("start") or not args.get("end"):
            raise ValueError("A precise trip needs both start and end (local YYYY-MM-DDTHH:MM).")
        form["newTripStart"] = local_datetime(args["start"], "start").strftime("%Y-%m-%dT%H:%M")
        form["newTripEnd"] = local_datetime(args["end"], "end").strftime("%Y-%m-%dT%H:%M")
    elif mode == "onlyDate":
        form["onlyDate"] = iso_date(args["date"], "date").isoformat()
    elif mode == "unknown":
        if args["unknown"] not in ("past", "future"):
            raise ValueError("unknown must be 'past' or 'future'.")
        form["unknownType"] = args["unknown"]
    else:
        start_day = integer(args, "start_day", 1)
        if start_day is None:
            raise ValueError("A relative leg needs start_day (1 = the plan's first day).")
        end_day = integer(args, "end_day", 1) or start_day
        if end_day < start_day:
            raise ValueError("end_day can't be before start_day.")
        times = [args.get("start_time"), args.get("end_time")]
        if any(times) and not all(times):
            raise ValueError("Give both start_time and end_time, or neither.")
        form.update({
            "planStartDay": start_day,
            "planEndDay": end_day,
            "planStartTime": clock(times[0], "start_time") if times[0] else "",
            "planEndTime": clock(times[1], "end_time") if times[1] else "",
            "planWeekdays": weekdays_mask(args.get("weekdays")),
        })
    return form


def weekdays_mask(days):
    """The plan builder's planWeekdays bitmask (bit 0 = Monday), '' for every day."""
    if not days:
        return ""
    if not isinstance(days, list) or any(str(d).lower() not in WEEKDAYS for d in days):
        raise ValueError(f"weekdays must be a list of day names: {list(WEEKDAYS)}.")
    mask = sum(1 << WEEKDAYS.index(str(d).lower()) for d in set(days))
    return "" if mask == 0b1111111 else mask


def weekdays_list(mask):
    return [d for i, d in enumerate(WEEKDAYS) if mask and mask & (1 << i)] or None


def check_order(form, path):
    """Arrival not before departure, compared in UTC as processDates stores them (a
    westbound flight may land at an earlier local clock time)."""
    if form.get("precision") == "preciseDates":
        start = getUtcDatetime(dateTime=datetime.fromisoformat(form["newTripStart"]), **path[0])
        end = getUtcDatetime(dateTime=datetime.fromisoformat(form["newTripEnd"]), **path[-1])
        if end < start:
            raise ValueError("end is before start (compared in UTC, each in its own place's time zone).")
    if form.get("precision") == "relative" and form.get("planStartTime") and form.get("planEndTime"):
        ref = date(2000, 1, 1)
        start = getUtcDatetime(dateTime=datetime.combine(
            ref + timedelta(days=form["planStartDay"] - 1),
            datetime.strptime(form["planStartTime"], "%H:%M").time()), **path[0])
        end = getUtcDatetime(dateTime=datetime.combine(
            ref + timedelta(days=form["planEndDay"] - 1),
            datetime.strptime(form["planEndTime"], "%H:%M").time()), **path[-1])
        if end < start:
            raise ValueError("The arrival is before the departure; set end_day for an overnight leg.")


def delay_form(args, precision):
    """departure_delay/arrival_delay in seconds from minutes, as processFormDates
    does; the form only offers delays on a trip with precise times."""
    out = {}
    for which in ("departure", "arrival"):
        minutes = number(args, f"{which}_delay_minutes")
        if minutes is None:
            continue
        if precision != "preciseDates":
            raise ValueError("Delays are only for trips with precise times (start/end).")
        out[f"{which}_delay"] = int(round(minutes * 60))
    return out


# ── Transitous runs ──────────────────────────────────────────────────────────────


def run_form(run_id, trip_type, origin, destination):
    """What picking a departure on the new-trip form fills in (apply() in new.html):
    precise local times, operator, line, platforms, delays of a started run, and the
    run's stops as via waypoints with their timetable records."""
    if trip_type not in TRANSIT_MODES:
        raise ValueError(f"transitous_trip_id only works for {sorted(TRANSIT_MODES)} trips.")
    try:
        segment = run_segment(run_id, (origin["lat"], origin["lng"]),
                              (destination["lat"], destination["lng"]))
    except (RateLimited, OutsideTimetable, requests.RequestException) as e:
        raise ValueError(f"Could not fetch the run from Transitous ({e}). Try again later.")
    board, alight = segment["board"], segment["alight"]
    start, end = board["dep"] or board["arr"], alight["arr"] or alight["dep"]
    if not start or not end:
        raise ValueError("The run has no times at those stops.")

    def local(utc_value, place):
        when = datetime.strptime(utc_value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC"))
        if place.get("tz"):
            return when.astimezone(ZoneInfo(place["tz"])).strftime("%Y-%m-%dT%H:%M")
        return getLocalDatetime(place["lat"], place["lng"], when).strftime("%Y-%m-%dT%H:%M")

    def delay(live, scheduled):
        if not live or not scheduled:
            return None
        diff = datetime.strptime(live, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(scheduled, "%Y-%m-%dT%H:%M:%SZ")
        return int(diff.total_seconds()) or None

    return {
        "timing": {"precision": "preciseDates", "onlyDateDuration": "",
                   "newTripStart": local(start, board), "newTripEnd": local(end, alight)},
        "operator": segment["operator"],
        "lineName": segment["line"] or None,
        "departurePlatform": board["platform_rt"] or board["platform"],
        "arrivalPlatform": alight["platform_rt"] or alight["platform"],
        "departure_delay": delay(board["dep_rt"], board["dep"]),
        "arrival_delay": delay(alight["arr_rt"], alight["arr"]),
        "vias": segment["stops"],
    }


# ── details ──────────────────────────────────────────────────────────────────────


def check_currency(username, currency):
    codes = [c["currency"] for c in get_available_currencies(username)]
    currency = str(currency).strip().upper()
    if currency not in codes:
        raise ValueError(f"Unknown currency {currency!r}. Use list_currencies for the valid codes.")
    return currency


def check_visibility(value):
    if value not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {list(VISIBILITIES)}.")
    return value


def check_power_type(trip_type, value):
    offered = POWER_TYPES.get(trip_type)
    if not offered:
        raise ValueError(f"A {trip_type} trip has no power type.")
    if value not in offered:
        raise ValueError(f"power_type for a {trip_type} trip is one of {list(offered)}.")
    return value


def check_tag_ids(username, tag_ids):
    if not isinstance(tag_ids, list):
        raise ValueError("tag_ids must be a list of tag ids (from list_tags).")
    with pg_session() as pg:
        refused = [t for t in tag_ids
                   if not str(t).isdigit() or site().get_tag_role(pg, int(t), username) is None]
    if refused:
        raise ValueError(f"No tag(s) {refused} of yours (or shared with you). See list_tags.")
    return [int(t) for t in tag_ids]


def check_ticket_ids(username, ticket_ids):
    if not isinstance(ticket_ids, list) or not all(str(t).isdigit() for t in ticket_ids):
        raise ValueError("ticket_ids must be a list of ticket ids (from list_tickets).")
    ids = sorted({int(t) for t in ticket_ids})
    if ids:
        with pg_session() as pg:
            mine = {r[0] for r in pg.execute(
                "SELECT uid FROM tickets WHERE uid = ANY(:ids) AND username = :username",
                {"ids": ids, "username": username})}
        missing = [t for t in ids if t not in mine]
        if missing:
            raise ValueError(f"No ticket(s) {missing} of yours. See list_tickets.")
    return ids


def visible_trainset_units(name, username):
    """Units of the trainset `name` this user may use, or None if there is none."""
    with pg_session() as pg:
        row = pg.execute(
            "SELECT units_json FROM trainsets WHERE name = :name AND (is_admin OR username = :username)"
            " ORDER BY (username = :username AND NOT is_admin) DESC LIMIT 1",
            {"name": name, "username": username},
        ).fetchone()
    return None if row is None else json.loads(row["units_json"] or "[]")


def check_trainset(username, trip_type, value):
    """material_type_advanced as the trainset builder saves it, and its car count:
    a saved set's name ("save & use"), a {"trainsets": [names], "reversed": [bools]}
    composite, or a one-off list of wagons ("use once") as slim units."""
    if trip_type not in TRAINSET_TYPES:
        raise ValueError(f"Trainsets are for {list(TRAINSET_TYPES)} trips.")
    if isinstance(value, str):
        units = visible_trainset_units(value.strip(), username)
        if units is None:
            raise ValueError(f"No trainset named {value!r} you can use. See list_trainsets.")
        return value.strip(), len(units)
    if isinstance(value, dict) and "trainsets" in value:
        names, flags = value.get("trainsets"), value.get("reversed") or []
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            raise ValueError("trainsets must be a non-empty list of trainset names.")
        if not isinstance(flags, list) or len(flags) > len(names):
            raise ValueError("reversed must be a list of booleans, one per trainset.")
        count = 0
        for name in names:
            units = visible_trainset_units(name, username)
            if units is None:
                raise ValueError(f"No trainset named {name!r} you can use. See list_trainsets.")
            count += len(units)
        composite = {"trainsets": names}
        if any(flags):
            composite["reversed"] = [bool(f) for f in flags] + [False] * (len(names) - len(flags))
        return json.dumps(composite), count
    if isinstance(value, dict) and "wagons" in value:
        wagons = value["wagons"]
        if not isinstance(wagons, list) or not wagons:
            raise ValueError("wagons must be a non-empty list of {wagon, side}.")
        units = []
        for i, w in enumerate(wagons):
            w = w if isinstance(w, dict) else {"wagon": w}
            side = w.get("side", "L")
            if side not in ("L", "R"):
                raise ValueError(f"wagons[{i}].side must be 'L' or 'R'.")
            units.append({"name": w.get("wagon"), "_side": side})
        with pg_session() as pg:
            known = {r[0] for r in pg.execute("SELECT name FROM wagons WHERE name = ANY(:n)",
                                              {"n": [u["name"] for u in units]})}
        unknown = [u["name"] for u in units if u["name"] not in known]
        if unknown:
            raise ValueError(f"No wagon(s) {unknown}. See search_wagons.")
        return json.dumps(units), len(units)
    raise ValueError(
        "trainset must be a saved trainset name, {trainsets: [names], reversed?: [bools]}, "
        "or {wagons: [{wagon, side}]}."
    )


def trainset_car_count(username, stored):
    """Cars of a stored material_type_advanced, for checking seat_car."""
    if not stored:
        return 0
    try:
        parsed = json.loads(stored)
    except (TypeError, ValueError):
        parsed = stored
    if isinstance(parsed, list):
        return len(parsed)
    names = parsed.get("trainsets", []) if isinstance(parsed, dict) else [parsed]
    return sum(len(visible_trainset_units(n, username) or []) for n in names)


def details_form(user, trip_type, args, current=None, plan=False):
    """The details of the form (detailsGroup + operator/line) as form fields, from the
    MCP arguments present in `args`. `current` is the stored trip's form (an edit),
    against which seat_car is checked when the trainset doesn't change."""
    out = {}
    if "operator" in args:
        out["operator"] = text(args, "operator", 500) or ""
    if "line_name" in args:
        out["lineName"] = text(args, "line_name", 200) or ""
    for key, field in (("departure_platform", "departurePlatform"), ("arrival_platform", "arrivalPlatform")):
        if key in args:
            if plan:
                raise ValueError(f"Plan legs have no {key}.")
            out[field] = text(args, key, 50) or ""
    if "visibility" in args:
        out["visibility"] = check_visibility(args["visibility"])
    if "material_type" in args:
        out["material_type"] = text(args, "material_type", 200) or ""
    if "reg" in args:
        out["reg"] = text(args, "reg", 100) or ""
    if "seat" in args:
        out["seat"] = text(args, "seat", 100) or ""
    if "notes" in args:
        out["notes"] = text(args, "notes", 10000) or ""

    cars = None
    if "trainset" in args:
        if args["trainset"] in (None, ""):
            out["material_type_advanced"], cars = "", 0
        else:
            out["material_type_advanced"], cars = check_trainset(user.username, trip_type, args["trainset"])
    if "seat_car" in args:
        if args["seat_car"] in (None, ""):
            out["seat_car"] = ""
        else:
            index = integer(args, "seat_car", 0)
            if cars is None:
                cars = trainset_car_count(user.username, (current or {}).get("material_type_advanced"))
            if index >= cars:
                raise ValueError("seat_car is the index (0 = first) of a car of the trip's trainset; "
                                 f"it has {cars} car(s).")
            out["seat_car"] = str(index)
    elif cars is not None and (current or {}).get("seat_car") not in (None, ""):
        out["seat_car"] = ""  # the builder clears the marked car with a new trainset

    if "price" in args:
        price = number(args, "price", 0)
        out["price"] = "" if price is None else price
    if "currency" in args and args["currency"] not in (None, ""):
        out["currency"] = check_currency(user.username, args["currency"])
    if "purchase_date" in args and args["purchase_date"] not in (None, ""):
        when = iso_date(args["purchase_date"], "purchase_date")
        if when > date.today():
            raise ValueError("purchase_date can't be in the future.")
        out["purchasing_date"] = when.isoformat()

    if "power_type" in args:
        out["powerType"] = check_power_type(trip_type, args["power_type"])
    if "co2_override" in args:
        if args["co2_override"] in (None, ""):
            out["co2Override"] = ""
        elif trip_type not in CO2_OVERRIDE_TYPES:
            raise ValueError("co2_override is for ferry trips only.")
        else:
            out["co2Override"] = number(args, "co2_override", 0)
    if "booked" in args:
        if not plan:
            raise ValueError("booked is for plan legs only.")
        out["booked"] = "true" if args["booked"] else ""
    if not plan:
        if "tag_ids" in args:
            out["tag_ids"] = check_tag_ids(user.username, args["tag_ids"] or [])
        if "ticket_ids" in args:
            out["ticket_ids"] = check_ticket_ids(user.username, args["ticket_ids"] or [])
    elif "tag_ids" in args or "ticket_ids" in args:
        raise ValueError("Plan legs have no tags or tickets.")
    return out


# ── whole trips ──────────────────────────────────────────────────────────────────


def new_trip_form(user, args, plan=False):
    """(newTrip, path, notes) — what routing.html posts to saveTrip (or savePlanTrip)
    for the trip the MCP arguments describe, and remarks for the AI."""
    trip_type = check_type(args.get("type") or "train")
    origin = resolve_place(args.get("origin"), trip_type, "origin")
    if trip_type in SINGLE_POINT_TYPES:
        if args.get("destination"):
            raise ValueError(f"A {trip_type} is one place: give only the origin.")
        destination = origin
    else:
        destination = resolve_place(args.get("destination"), trip_type, "destination")

    remarks = []
    run_id = text(args, "transitous_trip_id", 300)
    run = None
    if run_id:
        if args.get("vias"):
            raise ValueError("A Transitous run brings its own stops; don't pass vias with it.")
        run = run_form(run_id, trip_type, origin, destination)
        given = {k for k in TIMING_KEYS if args.get(k) not in (None, "")}
        if given:
            raise ValueError(f"A Transitous run sets the times; drop {sorted(given)}.")
        vias = run["vias"]
        timing = run["timing"]
    else:
        vias = resolve_vias(args.get("vias"), trip_type)
        timing = timing_form(args, plan=plan) or (
            {"precision": "relative", "planStartDay": 1, "planEndDay": 1, "planStartTime": "",
             "planEndTime": "", "planWeekdays": "", "onlyDateDuration": ""} if plan
            else None)
        if timing is None:
            raise ValueError("Give the trip's timing: start+end (precise), date (date only) or unknown='past'|'future'.")

    routed = route(origin, destination, vias, trip_type)
    check_order(timing, routed["path"])

    form = {
        "type": trip_type,
        "originStation": [[origin["lat"], origin["lng"]], origin["label"]],
        "destinationStation": [[destination["lat"], destination["lng"]], destination["label"]],
        "originStationKey": origin["key"] or "",
        "destinationStationKey": destination["key"] or "",
        **timing,
        "trip_length": routed["trip_length"],
        "estimated_trip_duration": routed["estimated_trip_duration"],
        "route_source": routed["route_source"],
        # The form's defaults: every field present, the selects on their first choice.
        "operator": "", "lineName": "", "material_type": "", "material_type_advanced": "",
        "reg": "", "seat": "", "seat_car": "", "notes": "", "price": "",
        "currency": user.user_currency or "EUR", "purchasing_date": "",
        "visibility": get_default_trip_visibility(trip_type),
    }
    if routed["details"]:
        form["details"] = routed["details"]
    if trip_type in POWER_TYPES:
        form["powerType"] = POWER_TYPES[trip_type][0]
    if vias:
        form["waypoints"] = json.dumps(vias, ensure_ascii=False)
    if run:
        for key in ("operator", "lineName", "departurePlatform", "arrivalPlatform"):
            if run[key]:
                form[key] = run[key]
        for key in ("departure_delay", "arrival_delay"):
            if run[key] is not None:
                form[key] = run[key]
    form.update(delay_form(args, timing["precision"]))
    form.update(details_form(user, trip_type, args, plan=plan))
    if form.get("price") not in ("", None) and not form.get("purchasing_date") and not plan:
        # The form's purchase date starts on today.
        form["purchasing_date"] = date.today().isoformat()
    if trip_type == "air" and form["material_type"]:
        remarks += aircraft_remarks(form["material_type"])
    if routed["straight_line"]:
        remarks.insert(0, STRAIGHT_LINE_REMARK)
    return form, routed["path"], remarks


def aircraft_remarks(code):
    with pg_session() as pg:
        known = pg.execute("SELECT 1 FROM airliners WHERE iata = :c", {"c": code}).fetchone()
    return [] if known else [
        f"Aircraft type {code!r} is not in Trainlog's aircraft list (search_material_types "
        "with type 'air'); it is saved as typed but shows without a name."
    ]


def stored_path(pg, table, key, value):
    row = pg.execute(f"SELECT ST_AsGeoJSON(geom) AS g FROM {table} WHERE {key} = :v", {"v": value}).fetchone()
    coords = geom_geojson_to_coords(row["g"]) if row and row["g"] else []
    return [{"lat": c[0], "lng": c[1]} for c in coords]


def route_edit(user, args, trip_type, current_path, current_waypoints, origin_label,
               destination_label, keys):
    """Re-route an edited trip when its ends or vias change (the edit page's map dialog):
    the route fields to post, the new ends, or None when the route is untouched."""
    if not any(k in args for k in ("origin", "destination", "vias", "transitous_trip_id")):
        return None
    if trip_type in SINGLE_POINT_TYPES and args.get("destination"):
        raise ValueError(f"A {trip_type} is one place: change only the origin.")

    def end(role, index, label, key):
        if args.get(role):
            return resolve_place(args[role], trip_type, role)
        point = current_path[index]
        return {"label": label, "lat": point["lat"], "lng": point["lng"], "key": key}

    origin = end("origin", 0, origin_label, keys[0])
    destination = origin if trip_type in SINGLE_POINT_TYPES else \
        end("destination", -1, destination_label, keys[1])
    run = None
    if text(args, "transitous_trip_id", 300):
        if "vias" in args:
            raise ValueError("A Transitous run brings its own stops; don't pass vias with it.")
        run = run_form(text(args, "transitous_trip_id", 300), trip_type, origin, destination)
        vias = run["vias"]
    elif "vias" in args:
        vias = resolve_vias(args["vias"], trip_type)
    else:
        vias = [w for w in current_waypoints if isinstance(w, dict) and "lat" in w and "lng" in w]
    routed = route(origin, destination, vias, trip_type)
    return {"origin": origin, "destination": destination, "vias": vias, "routed": routed, "run": run}


# ── edits ────────────────────────────────────────────────────────────────────────

# details_form's form fields, as the `trips` columns patchTrip takes.
FORM_TO_COLUMN = {
    "operator": "operator", "lineName": "line_name", "departurePlatform": "departure_platform",
    "arrivalPlatform": "arrival_platform", "visibility": "visibility",
    "material_type": "material_type", "material_type_advanced": "material_type_advanced",
    "reg": "reg", "seat": "seat", "seat_car": "seat_car", "notes": "notes", "price": "price",
    "currency": "currency", "purchasing_date": "purchasing_date", "powerType": "power_type",
    "co2Override": "co2_override", "ticket_ids": "ticket_ids",
}


def rename_only(place):
    """A place given by name alone: the station renamed, its point (and route) kept."""
    return isinstance(place, dict) and place.get("name") and not any(
        place.get(k) not in (None, "") for k in ("lat", "lng", "iata"))


def stored_timing(start, end, manual_duration):
    """The MCP timing arguments a stored trip's dates read as (for an edit that changes
    one end of a precise trip: the other is kept)."""
    if not isinstance(start, datetime):
        return {}
    if start.second == 1:
        return {"date": start.date().isoformat()}
    return {"start": start.strftime("%Y-%m-%dT%H:%M"),
            "end": (end if isinstance(end, datetime) else start).strftime("%Y-%m-%dT%H:%M")}


def timing_columns(form):
    """A form timing as the start_datetime/end_datetime/manual_trip_duration columns
    patchTrip takes (1/-1 for no date, :01 seconds for a date without a time)."""
    precision = form["precision"]
    duration = form.get("onlyDateDuration")
    columns = {"manual_trip_duration": duration if duration not in ("", None) else None}
    if precision == "preciseDates":
        columns["start_datetime"] = form["newTripStart"].replace("T", " ") + ":00"
        columns["end_datetime"] = form["newTripEnd"].replace("T", " ") + ":00"
        columns["manual_trip_duration"] = None
    elif precision == "onlyDate":
        columns["start_datetime"] = columns["end_datetime"] = form["onlyDate"] + " 00:00:01"
    else:
        columns["start_datetime"] = columns["end_datetime"] = 1 if form["unknownType"] == "future" else -1
    return columns


def trip_patch(user, trip_id, args):
    """(new type or None, patchTrip payload, tag ids or None, remarks) for update_trip:
    the edit page's changes, checked as the page checks them."""
    stored = site().get_trip_pg(trip_id)
    trip_type = stored["type"]
    new_type = None
    if args.get("type") and args["type"] != trip_type:
        check_type(args["type"])
        if not TripTypes.can_transform(TripTypes(trip_type), TripTypes(args["type"])):
            raise ValueError(f"A {trip_type} trip can't become a {args['type']} trip "
                             "(only within the same family, as on the trips list).")
        new_type = trip_type = args["type"]

    with pg_session() as pg:
        path = stored_path(pg, "paths", "trip_id", trip_id)
    args = dict(args)
    patch, remarks = {}, []

    for role, column, index in (("origin", "origin_station", 0), ("destination", "destination_station", -1)):
        if rename_only(args.get(role)):
            patch[column] = flagged(args.pop(role)["name"], path[index]["lat"], path[index]["lng"])
            if trip_type in SINGLE_POINT_TYPES and role == "origin":
                patch["destination_station"] = patch[column]

    try:
        waypoints = json.loads(stored.get("waypoints") or "[]")
    except (TypeError, ValueError):
        waypoints = []
    edit = route_edit(
        user, args, trip_type, path, waypoints if isinstance(waypoints, list) else [],
        patch.get("origin_station", stored["origin_station"]),
        patch.get("destination_station", stored["destination_station"]),
        (stored.get("origin_station_key"), stored.get("destination_station_key")),
    )
    if edit:
        routed = edit["routed"]
        path = routed["path"]
        patch.update({
            "path": path,
            "trip_length": routed["trip_length"],
            "estimated_trip_duration": routed["estimated_trip_duration"],
            "waypoints": json.dumps(edit["vias"], ensure_ascii=False) if edit["vias"] else "[]",
            "origin_station": edit["origin"]["label"],
            "destination_station": edit["destination"]["label"],
            "origin_station_key": edit["origin"]["key"],
            "destination_station_key": edit["destination"]["key"],
            "route_source": routed["route_source"],
        })
        if routed["details"]:
            patch["details"] = routed["details"]
        if routed["straight_line"]:
            remarks.append(STRAIGHT_LINE_REMARK)

    # Timing: one end of a precise trip may change alone, the other kept.
    start = site()._as_datetime(stored["start_datetime"])
    end = site()._as_datetime(stored["end_datetime"])
    if edit and edit["run"]:
        given = {k for k in TIMING_KEYS if args.get(k) not in (None, "")}
        if given:
            raise ValueError(f"A Transitous run sets the times; drop {sorted(given)}.")
        timing = edit["run"]["timing"]
        run = edit["run"]
        for key, column in (("operator", "operator"), ("lineName", "line_name"),
                            ("departurePlatform", "departure_platform"),
                            ("arrivalPlatform", "arrival_platform")):
            if run[key]:
                patch[column] = run[key]
        patch["departure_delay"] = run["departure_delay"]
        patch["arrival_delay"] = run["arrival_delay"]
    else:
        timing_args = dict(args)
        if ("start" in args) != ("end" in args) and start and start.second != 1:
            timing_args = {**stored_timing(start, end, None), **{k: args[k] for k in ("start", "end") if k in args}}
        timing = timing_form(timing_args)
    if timing:
        check_order(timing, path)
        patch.update(timing_columns(timing))
    elif edit:
        current = {"precision": "preciseDates", **{
            "newTripStart": start.strftime("%Y-%m-%dT%H:%M"),
            "newTripEnd": (end or start).strftime("%Y-%m-%dT%H:%M")}} if start and start.second != 1 else {}
        if current:
            check_order(current, path)

    precision = timing["precision"] if timing else (
        "preciseDates" if start and start.second != 1 else "other")
    for which in ("departure", "arrival"):
        key = f"{which}_delay_minutes"
        if key in args:
            if args[key] in (None, ""):
                patch[f"{which}_delay"] = None
            else:
                patch.update(delay_form({key: args[key]}, precision))
    if timing and precision != "preciseDates":
        patch["departure_delay"] = patch["arrival_delay"] = None

    current = {"material_type_advanced": stored.get("material_type_advanced"),
               "seat_car": stored.get("seat_car")}
    details = details_form(user, trip_type, args, current=current)
    tag_ids = details.pop("tag_ids", None)
    for field, value in details.items():
        patch[FORM_TO_COLUMN[field]] = None if value == "" else value
    if patch.get("price") is not None and not (patch.get("currency") or stored.get("currency")):
        patch["currency"] = user.user_currency or "EUR"
    if patch.get("power_type") is None and edit and trip_type in POWER_TYPES and not stored.get("power_type"):
        patch["power_type"] = POWER_TYPES[trip_type][0]
    if trip_type == "air" and patch.get("material_type"):
        remarks += aircraft_remarks(patch["material_type"])
    return new_type, patch, tag_ids, remarks


def plan_form_from_row(pt):
    """The plan editor's payload (edit_copy → edit_full) for a stored plan-trip, as the
    editor would post it unchanged."""
    form = {
        "origin_station": pt["origin_station"], "destination_station": pt["destination_station"],
        "operator": pt["operator"] or "", "lineName": pt["line_name"] or "",
        "material_type": pt["material_type"] or "",
        "material_type_advanced": pt["material_type_advanced"] or "",
        "reg": pt["reg"] or "", "seat": pt["seat"] or "", "notes": pt["notes"] or "",
        "price": "" if pt["price"] is None else pt["price"], "currency": pt["currency"] or "",
        "purchasing_date": pt["purchase_date"].strftime("%Y-%m-%d") if pt["purchase_date"] else "",
        "booked": "true" if pt["booked"] else "",
        "waypoints": pt["waypoints"] or "", "visibility": pt["visibility"] or "",
        "powerType": pt["power_type"] or "",
        "co2Override": "" if pt["co2_override"] is None else pt["co2_override"],
        "onlyDateDuration": "" if pt["manual_trip_duration"] is None else pt["manual_trip_duration"],
    }
    mode = pt["timing_mode"] or "unknown"
    form["precision"] = mode
    if mode == "relative":
        form.update({
            "planStartDay": pt["start_day"], "planEndDay": pt["end_day"] or pt["start_day"],
            "planStartTime": pt["start_time"].strftime("%H:%M") if pt["start_time"] else "",
            "planEndTime": pt["end_time"].strftime("%H:%M") if pt["end_time"] else "",
            "planWeekdays": pt["weekdays"] or "",
        })
    elif mode == "preciseDates":
        form["newTripStart"] = pt["start_datetime"].strftime("%Y-%m-%dT%H:%M")
        form["newTripEnd"] = (pt["end_datetime"] or pt["start_datetime"]).strftime("%Y-%m-%dT%H:%M")
    elif mode == "onlyDate":
        form["onlyDate"] = pt["start_datetime"].strftime("%Y-%m-%d")
    else:
        form["unknownType"] = "future"
    return form


def plan_stored_timing(pt):
    """The MCP timing arguments of a stored relative/precise leg, to fill an edit that
    changes some of them."""
    if pt["timing_mode"] == "relative":
        return {k: v for k, v in {
            "start_day": pt["start_day"], "end_day": pt["end_day"],
            "start_time": pt["start_time"].strftime("%H:%M") if pt["start_time"] else None,
            "end_time": pt["end_time"].strftime("%H:%M") if pt["end_time"] else None,
            "weekdays": weekdays_list(pt["weekdays"]),
        }.items() if v is not None}
    if pt["timing_mode"] == "preciseDates" and pt["start_datetime"]:
        return stored_timing(pt["start_datetime"], pt["end_datetime"], None)
    return {}


def plan_trip_edit_form(user, pt, args):
    """(edit_full payload, remarks) for update_plan_trip: the stored leg's payload with
    the MCP arguments' changes, checked as the editor checks them."""
    trip_type = pt["trip_type"]
    if args.get("type") and args["type"] != trip_type:
        raise ValueError("A plan leg's type can't be changed; delete it and add a new one.")
    if "seat_car" in args:
        raise ValueError("Plan legs have no marked car (seat_car).")
    form = plan_form_from_row(pt)
    path = [{"lat": c[0], "lng": c[1]} for c in geom_geojson_to_coords(pt["geojson"])]
    args = dict(args)
    remarks = []

    for role, column, index in (("origin", "origin_station", 0), ("destination", "destination_station", -1)):
        if rename_only(args.get(role)):
            form[column] = flagged(args.pop(role)["name"], path[index]["lat"], path[index]["lng"])
            if trip_type in SINGLE_POINT_TYPES and role == "origin":
                form["destination_station"] = form[column]
    try:
        waypoints = json.loads(pt["waypoints"] or "[]")
    except (TypeError, ValueError):
        waypoints = []
    edit = route_edit(user, args, trip_type, path, waypoints if isinstance(waypoints, list) else [],
                      form["origin_station"], form["destination_station"], (None, None))
    if edit:
        routed = edit["routed"]
        path = routed["path"]
        form.update({
            "path": json.dumps(path),
            "trip_length": routed["trip_length"],
            "estimated_trip_duration": routed["estimated_trip_duration"],
            "waypoints": json.dumps(edit["vias"], ensure_ascii=False) if edit["vias"] else "[]",
            "origin_station": edit["origin"]["label"],
            "destination_station": edit["destination"]["label"],
        })
        if routed["details"]:
            form["details"] = json.dumps(routed["details"])
        if routed["straight_line"]:
            remarks.append(STRAIGHT_LINE_REMARK)
        if trip_type in POWER_TYPES and not form["powerType"]:
            form["powerType"] = POWER_TYPES[trip_type][0]

    if args.get("clear_timing"):
        if any(args.get(k) not in (None, "") for k in TIMING_KEYS):
            raise ValueError("clear_timing can't go with other timing fields.")
        form.update({"precision": "unknown", "unknownType": "future", "onlyDateDuration": ""})
    elif edit and edit["run"]:
        if any(args.get(k) not in (None, "") for k in TIMING_KEYS):
            raise ValueError("A Transitous run sets the times; drop the timing fields.")
        form.update(edit["run"]["timing"])
        for key in ("operator", "lineName"):
            if edit["run"][key]:
                form[key] = edit["run"][key]
    else:
        given = {k for k in TIMING_KEYS if args.get(k) not in (None, "")}
        relative = given & {"start_day", "end_day", "start_time", "end_time", "weekdays"}
        precise = given & {"start", "end"}
        timing_args = {k: args[k] for k in given}
        # Fields the leg's own mode keeps when only some are sent (start_time alone keeps
        # the day; start_day alone moves end_day with it).
        stored = plan_stored_timing(pt)
        if relative and pt["timing_mode"] == "relative":
            if "start_day" in given and "end_day" not in given:
                timing_args["end_day"] = int(timing_args["start_day"]) + (pt["end_day"] or pt["start_day"]) - pt["start_day"]
            timing_args = {**stored, **timing_args}
            for key in ("start_time", "end_time"):
                if key in args and args[key] in (None, ""):
                    timing_args.pop(key, None)
            if "weekdays" in args and not args["weekdays"]:
                timing_args.pop("weekdays", None)
        elif precise and len(precise) == 1 and pt["timing_mode"] == "preciseDates":
            timing_args = {**stored, **timing_args}
        if "duration_minutes" in args and not given - {"duration_minutes"}:
            # A duration alone keeps the leg's day(s) or date.
            timing_args = {**stored, **timing_args} if pt["timing_mode"] in ("relative", "onlyDate") else timing_args
            if pt["timing_mode"] == "onlyDate":
                timing_args["date"] = pt["start_datetime"].strftime("%Y-%m-%d")
            elif pt["timing_mode"] == "unknown":
                timing_args["unknown"] = "future"
        timing = timing_form(timing_args, plan=True)
        if timing:
            for key in ("planStartDay", "planEndDay", "planStartTime", "planEndTime", "planWeekdays",
                        "newTripStart", "newTripEnd", "onlyDate", "unknownType"):
                form.pop(key, None)
            form.update(timing)
    check_order(form, path)

    details = details_form(user, trip_type, args, plan=True)
    form.update(details)
    has_price = form.get("price") not in (None, "")
    if has_price and not form.get("currency"):
        form["currency"] = user.user_currency or "EUR"
    if form.get("booked") and not has_price:
        raise ValueError("booked needs the leg's price (the editor only books a priced leg).")
    if trip_type == "air" and details.get("material_type"):
        remarks += aircraft_remarks(details["material_type"])
    return form, remarks
