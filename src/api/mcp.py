"""src/api/mcp.py — MCP server as a Flask Blueprint (Streamable HTTP transport).

Single POST endpoint — no persistent connections, WSGI-friendly. Lets an external
AI manage a user's trips and plans with what the website offers, no more and no less:
every write goes through the save code the site's own forms post to (saveTripToDb,
patchTrip's apply_trip_patch, savePlanTripToDb, the plan editor's save_plan_trip_edit),
after src/api/mcp_form.py has checked the arguments the way the forms check them in
the browser. The lookups (station search, departures, operators, material types,
trainsets, wagons, currencies, tags, tickets) are the ones the forms autocomplete from.

Auth: per-user mcp_token (User.mcp_token), accepted as ?api_key=, X-API-Key header
or Authorization: Bearer. Mint/rotate it from /u/<username>/mcp.

Claude Desktop config (~/.config/Claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "trainlog": {
          "type": "streamable-http",
          "url": "https://<host>/mcp?api_key=<your-token>"
        }
      }
    }
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, request
from flask.globals import request_ctx
from flask.sessions import SecureCookieSession
from werkzeug.exceptions import HTTPException

from src.api import mcp_form as form
from src.api.motis import TRANSIT_MODES, departures_payload
from src.consts import TripTypes
from src.currency import get_available_currencies
from src.pg import pg_session
from src.plans.delete_plan import delete_plan as _delete_plan
from src.plans.delete_plan import delete_plan_trip as _delete_plan_trip
from src.plans.duplicate_plan import duplicate_plan as _duplicate_plan
from src.plans.import_trips import import_trips_to_plan
from src.plans.validate_plan import validate_plan as _validate_plan
from src.sql.plans import (
    archive_plan_query,
    delete_plan_cost_query,
    get_plan_costs_query,
    get_plan_query,
    get_plan_trips_query,
    get_user_plans_query,
    insert_plan_cost_query,
    insert_plan_query,
    reorder_plan_trip_query,
    set_plan_trip_cost_query,
    update_plan_cost_query,
    update_plan_query,
    update_plan_visibility_query,
)
from src.sql.stations import get_airports_query
from src.sql.trips import get_material_types_query, get_operators_query
from src.trips import update_trip_type
from src.trips.delete_trip import delete_trip as _delete_trip
from src.users import Friendship, User
from src.utils import external_url

# Trips a friend is allowed to read. Mirrors the friend-facing read path
# (dashboard_friends): explicit public/friends only — NULL visibility is NOT
# exposed to friends.
FRIEND_VISIBLE = ("public", "friends")

logger = logging.getLogger(__name__)

blueprint = Blueprint("mcp", __name__, url_prefix="/mcp")

# The station search's type filter for each trip type, as new.html's URLs map sets it
# (`type` makes rail/bus/… search Trainlog's own stations when the user has that on).
STATION_SEARCH_PARAMS = {
    "bus": {"osm_tag": ["amenity:bus_station", "highway:bus_stop"], "type": ["bus"]},
    "train": {"osm_tag": ["railway:halt", "railway:station"], "type": ["train"]},
    "tram": {"osm_tag": ["railway:tram_stop", "railway:station", "railway:halt"], "type": ["tram"]},
    "metro": {"osm_tag": ["railway:station", "railway:subway_entrance"], "type": ["metro"]},
    "funicular": {"osm_tag": ["railway:halt", "railway:station"], "type": ["funicular"]},
    "rail": {"osm_tag": ["railway:halt", "railway:station"], "type": ["rail"]},
    "ferry": {"osm_tag": ["amenity:ferry_terminal"], "type": ["ferry"]},
    "helicopter": {"osm_tag": ["aeroway:helipad", "aeroway:heliport", "aeroway:aerodrome"]},
    "accommodation": {"osm_tag": [
        "tourism:alpine_hut", "tourism:apartment", "tourism:chalet", "tourism:guest_house",
        "tourism:hostel", "tourism:hotel", "tourism:motel", "tourism:wilderness_hut"]},
    "poi": {"osm_tag": [
        "tourism", "!tourism:alpine_hut", "!tourism:apartment", "!tourism:chalet",
        "!tourism:guest_house", "!tourism:hostel", "!tourism:hotel", "!tourism:motel",
        "!tourism:wilderness_hut"]},
    "restaurant": {"osm_tag": [
        "amenity:restaurant", "amenity:pub", "amenity:biergarten", "amenity:cafe", "amenity:bar"]},
    "aerialway": {"osm_tag": ["aerialway:station"], "type": ["aerialway"]},
    "ski": {"osm_tag": ["aerialway:station"], "type": ["ski"]},
}

# ── auth ──────────────────────────────────────────────────────────────────────


def _auth():
    key = (
        request.headers.get("X-API-Key")
        or request.headers.get("Authorization", "").removeprefix("Bearer ")
        or request.args.get("api_key", "")
    ).strip()
    if not key:
        return None
    return User.query.filter_by(mcp_token=key).first()


@contextmanager
def _acting_as(user):
    """Run the site's own code as the token's user: what it reads of the logged-in user
    (getUser, the station search settings, the currency…) comes from the session, so
    the request gets one of that user's for the duration. The request's own session is
    put back before the response is made, so no login cookie is ever issued."""
    ctx = request_ctx._get_current_object()
    original = ctx.session
    # The username key first, so no username can stand in for the other keys.
    ctx.session = SecureCookieSession({
        user.username: True,
        "logged_in": user.username,
        "userinfo": {"logged_in_user": user.username, "user_id": user.uid,
                     "lang": user.lang or "en", "is_premium": bool(user.premium)},
    })
    try:
        yield
    finally:
        ctx.session = original


def _are_friends(viewer_uid: int, owner_uid: int) -> bool:
    """True if viewer may read owner's friends-level trips.

    Accepting a friend request writes two reciprocal accepted rows, so an
    accepted (user_id=owner, friend_id=viewer) row is sufficient and correct.
    Unlike the legacy session helper we require accepted IS NOT NULL, so a
    *pending* request grants nothing."""
    if viewer_uid == owner_uid:
        return True
    return (
        Friendship.query.filter(
            Friendship.user_id == owner_uid,
            Friendship.friend_id == viewer_uid,
            Friendship.accepted.isnot(None),
        ).first()
        is not None
    )


def _owned_plan(plan_uuid: str, uid: int) -> dict:
    """Fetch a plan by uuid and verify the token user owns it. ValueError otherwise
    (never disclose plans owned by others)."""
    try:
        _uuid.UUID(str(plan_uuid))
    except ValueError:
        raise ValueError("Plan not found.")
    with pg_session() as pg:
        row = pg.execute(get_plan_query(), {"uuid": str(plan_uuid)}).fetchone()
    if row is None or row._mapping["user_id"] != uid:
        raise ValueError("Plan not found.")
    return dict(row._mapping)


def _owned_plan_trip(plan: dict, plan_trip_uid) -> dict:
    """One leg (with its path as GeoJSON) of an already-ownership-checked plan."""
    with pg_session() as pg:
        row = pg.execute(
            "SELECT *, ST_AsGeoJSON(geom) AS geojson FROM plan_trips"
            " WHERE uid = :uid AND plan_id = :pid",
            {"uid": int(plan_trip_uid), "pid": plan["uid"]},
        ).fetchone()
    if row is None:
        raise ValueError("Plan leg not found.")
    return dict(row._mapping)


def _owned_trip_id(trip_id, uid) -> int:
    try:
        trip_id = int(trip_id)
    except (TypeError, ValueError):
        raise ValueError("trip_id must be a number.")
    with pg_session() as pg:
        row = pg.execute("SELECT user_id FROM trips WHERE trip_id = :tid", {"tid": trip_id}).fetchone()
    if row is None or row["user_id"] != uid:
        raise ValueError("Trip not found.")
    return trip_id


# ── tool schemas ──────────────────────────────────────────────────────────────

PLACE = {
    "type": "object",
    "description": (
        "A place exactly as search_stations returned it: its `name`, `lat`, `lng` and, "
        "when given, `station_key`; for flights the airport's `iata` instead. For a "
        "place search_stations doesn't know, the user's own name and coordinates."
    ),
    "properties": {
        "name": {"type": "string"},
        "lat": {"type": "number"},
        "lng": {"type": "number"},
        "iata": {"type": "string", "description": "Airport IATA code (air / helicopter)."},
        "station_key": {"type": "string", "description": "From search_stations, when it has one."},
    },
}

VIA = {
    **PLACE,
    "properties": {
        **PLACE["properties"],
        "exact": {"type": "boolean", "description": "Route exactly through this point (rail only)."},
    },
}


def _timing_schema(plan=False):
    props = {
        "start": {"type": "string", "description": "Precise: local departure YYYY-MM-DDTHH:MM (with `end`)."},
        "end": {"type": "string", "description": "Precise: local arrival YYYY-MM-DDTHH:MM (with `start`)."},
        "date": {"type": "string", "description": "Date only: YYYY-MM-DD (no times known)."},
        "unknown": {"type": "string", "enum": ["past", "future"],
                    "description": "No date at all: a past trip, or a future/planned one."},
        "duration_minutes": {"type": "integer",
                             "description": "Duration of a trip without times (date / unknown"
                                            + (" / untimed relative" if plan else "") + ")."},
    }
    if plan:
        props.update({
            "start_day": {"type": "integer", "description": "Relative: day of departure (1 = anchor_date)."},
            "end_day": {"type": "integer", "description": "Relative: day of arrival (default start_day)."},
            "start_time": {"type": "string", "description": "Relative: local departure HH:MM (with end_time)."},
            "end_time": {"type": "string", "description": "Relative: local arrival HH:MM (with start_time)."},
            "weekdays": {"type": "array", "items": {"type": "string", "enum": list(form.WEEKDAYS)},
                         "description": "Relative: the days the service runs, when not daily."},
        })
    else:
        props.update({
            "departure_delay_minutes": {"type": "number", "description": "Precise trips only: minutes late leaving (negative = early)."},
            "arrival_delay_minutes": {"type": "number", "description": "Precise trips only: minutes late arriving."},
        })
    return props


def _details_schema(plan=False):
    props = {
        "operator": {"type": "string", "description": "Operator(s), comma-separated (search_operators)."},
        "line_name": {"type": "string", "description": "Line / train number / flight number."},
        "visibility": {"type": "string", "enum": list(form.VISIBILITIES),
                       "description": "Default: the trip type's default (list_trip_types)."},
        "material_type": {"type": "string",
                          "description": "Vehicle type, free text (search_material_types). For flights the ICAO aircraft code, e.g. A320."},
        "trainset": {
            "description": (
                "Train/metro/tram only — the trainset builder's composition: a trainset "
                "name (list_trainsets), {trainsets: [names], reversed?: [bools]} for "
                "coupled sets, or {wagons: [{wagon, side: 'L'|'R'}]} (search_wagons) for a "
                "one-off composition. null removes it."
            ),
        },
        "reg": {"type": "string", "description": "Registration / vehicle number (ships: IMO or MMSI)."},
        "seat": {"type": "string"},
        "notes": {"type": "string"},
        "price": {"type": "number", "description": "Price paid (≥ 0)."},
        "currency": {"type": "string", "description": "ISO code from list_currencies. Default: the user's currency."},
        "purchase_date": {"type": "string", "description": "YYYY-MM-DD, not in the future."},
        "power_type": {"type": "string", "enum": sorted({p for v in form.POWER_TYPES.values() for p in v}),
                       "description": "Only the values list_trip_types gives for the type."},
        "co2_override": {"type": "number", "description": "Ferry only: grams of CO2 per passenger-km."},
    }
    if plan:
        props["booked"] = {"type": "boolean", "description": "Ticket already bought."}
    else:
        props.update({
            "seat_car": {"type": "integer", "description": "Index (0 = first) of the car you sat in, in the trainset."},
            "departure_platform": {"type": "string"},
            "arrival_platform": {"type": "string"},
            "tag_ids": {"type": "array", "items": {"type": "integer"}, "description": "Tags (list_tags). Replaces the trip's tags."},
            "ticket_ids": {"type": "array", "items": {"type": "integer"}, "description": "Tickets this trip used (list_tickets)."},
        })
    return props


def _trip_write_schema(plan=False, update=False):
    props = {
        "type": {"type": "string", "enum": form.form_types(),
                 "description": "Trip type (default train)." + (
                     " Changing it is only possible within its family (rail types, bus/car, air/helicopter, cycle/scooter, places)."
                     if update else "")},
        "origin": PLACE,
        "destination": {**PLACE, "description": PLACE["description"] + " Omit for accommodation/restaurant/poi."},
        "vias": {"type": "array", "items": VIA, "description": "Places to route through, in order."},
        "transitous_trip_id": {
            "type": "string",
            "description": (
                "trip_id of a run from search_departures: sets the times, operator, line, "
                "platforms and the run's stops (boarded/left at the run's stops nearest "
                "origin/destination). Don't pass timing or vias with it."
            ),
        },
        **_timing_schema(plan),
        **_details_schema(plan),
    }
    if update:
        props["type"]["description"] = props["type"]["description"].replace(" (default train)", "")
        if plan:
            props.pop("type")
            props["clear_timing"] = {"type": "boolean", "description": "Make the leg undated."}
    return props


def _build_tools() -> list:
    return [
        # ── lookups ─────────────────────────────────────────────────────────
        {
            "name": "search_stations",
            "description": (
                "Find stations/stops/airports/places with the website's own station search "
                "(the same suggestions a user gets in the trip form). ALWAYS use it for "
                "the places of add_trip / add_plan_trip / update_*, with the SAME `type` "
                "as the trip, and pass the chosen result's name, lat, lng and station_key "
                "(or iata for flights) unchanged. Pass near_lat/near_lng (e.g. the trip's "
                "other end) to rank same-named stops nearby first."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "type": {"type": "string", "default": "train", "enum": form.form_types()},
                    "near_lat": {"type": "number"},
                    "near_lng": {"type": "number"},
                    "limit": {"type": "integer", "description": "Max results (default 8, max 20)."},
                },
                "required": ["query"],
            },
        },
        {
            "name": "search_departures",
            "description": (
                "Find the actual scheduled services between two stations in the "
                "public-transport timetables (Transitous / MOTIS): direct runs only, "
                "no changes. The same list the website shows on its new-trip form. "
                "Use it for train/tram/metro/bus/ferry/aerialway/funicular trips "
                "whenever the user gives a date (and ideally a time or a train "
                "number): pick the run that matches, then pass its `trip_id` to "
                "add_trip / add_plan_trip as `transitous_trip_id`, so the trip gets the "
                "real times, operator, line, platforms and every intermediate stop.\n\n"
                "Get the coordinates from search_stations first. Runs are listed "
                "from about 20 minutes before `time` (or the start of `date`) "
                "onwards; times are local at each stop (YYYY-MM-DDTHH:MM). Only "
                "dates inside the loaded timetables (roughly the coming weeks) "
                "have results; `outside_timetable: true` means none can be found."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "default": "train", "enum": sorted(TRANSIT_MODES)},
                    "origin_lat": {"type": "number"},
                    "origin_lng": {"type": "number"},
                    "destination_lat": {"type": "number"},
                    "destination_lng": {"type": "number"},
                    "date": {"type": "string", "description": "Departure date YYYY-MM-DD (local)."},
                    "time": {"type": "string", "description": "Local departure time HH:MM to search around."},
                    "limit": {"type": "integer", "description": "Max runs to return (default 15, max 60)."},
                },
                "required": ["origin_lat", "origin_lng", "destination_lat",
                             "destination_lng", "date"],
            },
        },
        {
            "name": "list_trip_types",
            "description": (
                "The trip types you can create, with what each one's form offers: its "
                "default visibility, power types (first = default), whether it takes a "
                "trainset, a CO2 override, timetable runs, vias, and whether it is a single "
                "place (no destination)."
            ),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "list_currencies",
            "description": "The currency codes a price can be in (the form's currency list), the user's own first.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "search_operators",
            "description": (
                "Operator names the trip form suggests for a type: the user's own past "
                "operators and the known operators (with logos). Prefer these spellings."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "default": "train", "enum": form.form_types()},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "Max results (default 20, max 100)."},
                },
                "required": [],
            },
        },
        {
            "name": "search_material_types",
            "description": (
                "Vehicle types (material_type) the trip form suggests: for flights, "
                "Trainlog's aircraft list (code + name; save the code); otherwise the "
                "values the user has typed before for that trip type."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "default": "train", "enum": form.form_types()},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "Max results (default 30, max 200)."},
                },
                "required": [],
            },
        },
        {
            "name": "list_trainsets",
            "description": (
                "Trainsets (saved train compositions) the user can put on a train/metro/"
                "tram trip as `trainset`: their own and the public ones, with their cars."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Filter by name."}},
                "required": [],
            },
        },
        {
            "name": "search_wagons",
            "description": (
                "Search the wagon/locomotive drawings of the trainset builder (all terms "
                "must match label/category/era/notes…). Use their `name` in "
                "trainset {wagons: [{wagon, side}]}."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "Max results (default 20, max 100)."},
                },
                "required": ["query"],
            },
        },
        {
            "name": "list_tags",
            "description": "The user's tags, and tags shared with them, for tag_ids.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "list_tickets",
            "description": "The user's tickets (passes, multi-trip tickets…), for ticket_ids.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "list_friends",
            "description": (
                "List the users you are friends with (accepted friendships). "
                "Their trips can be read via list_trips/get_trip, read-only."
            ),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        # ── trips ───────────────────────────────────────────────────────────
        {
            "name": "list_trips",
            "description": (
                "List trips, most recent first, as a compact summary per trip. "
                "By default lists the authenticated user's own trips (all "
                "visibilities, including price). Pass `username` to read a "
                "FRIEND's trips instead: this is read-only and only returns their "
                "public/friends-visibility trips, with private fields (price, "
                "notes, seat, booking refs) omitted. Use get_trip for everything about one."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "description": "Read this friend's trips (read-only). Omit for your own."},
                    "trip_type": {"type": "string", "enum": [t.value for t in TripTypes]},
                    "upcoming_only": {"type": "boolean", "description": "Only future / planned trips."},
                    "from_date": {"type": "string", "description": "Only trips starting on/after YYYY-MM-DD."},
                    "to_date": {"type": "string", "description": "Only trips starting on/before YYYY-MM-DD."},
                    "limit": {"type": "integer", "description": "Max trips to return (default 50, max 500)."},
                },
                "required": [],
            },
        },
        {
            "name": "get_trip",
            "description": (
                "Everything about one trip by id, in the same terms add_trip/update_trip "
                "take: places, timing, stops (with times and platforms), platforms, delays, "
                "material type, trainset, registration, seat, tags, tickets, price… A "
                "friend's public/friends trip can be read too, without its private fields."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"trip_id": {"type": "integer"}},
                "required": ["trip_id"],
            },
        },
        {
            "name": "add_trip",
            "description": (
                "Log a trip, as the website's new-trip form does. One trip = ONE segment "
                "(a flight with a connection is two trips).\n\n"
                "Places: from search_stations (same `type`), passed unchanged; flights by "
                "airport `iata`. Trainlog routes the trip like the website, adds each "
                "station's flag, splits the distance by country and works out time zones. "
                "When no route exists the trip is saved as straight lines and the result "
                "says `straight_line: true`: you MUST tell the user, who can redraw it.\n\n"
                "Timing, in exactly one way: `start`+`end` (local times), `date`, or "
                "`unknown`='past'|'future'. For a scheduled service, pass "
                "`transitous_trip_id` from search_departures instead: times, operator, "
                "line, platforms and every stop come from the timetable.\n\n"
                "Details are checked as the form checks them: currencies from "
                "list_currencies, power types and trainsets only where the type offers "
                "them (list_trip_types), tags and tickets of the user's.\n\n"
                "The result's `links` open the trip on a map and in the user's trip list: "
                "give them to the user. Relay any `remarks`."
            ),
            "inputSchema": {
                "type": "object",
                "properties": _trip_write_schema(),
                "required": ["origin"],
            },
        },
        {
            "name": "update_trip",
            "description": (
                "Edit a trip, as its edit page does: only the fields you pass change, "
                "null clears a detail. Passing origin/destination with coordinates (from "
                "search_stations), vias, or a transitous_trip_id re-routes the trip; "
                "passing a place's `name` alone just renames the station. Timing: the "
                "same ways as add_trip (on a precise trip, `start` or `end` alone keeps "
                "the other). tag_ids replaces the trip's tags."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"trip_id": {"type": "integer"}, **_trip_write_schema(update=True)},
                "required": ["trip_id"],
            },
        },
        {
            "name": "delete_trip",
            "description": "Permanently delete one of the user's trips.",
            "inputSchema": {
                "type": "object",
                "properties": {"trip_id": {"type": "integer"}},
                "required": ["trip_id"],
            },
        },
        # ── plans (parallel-world itineraries) ──────────────────────────────
        {
            "name": "list_plans",
            "description": (
                "List the user's plans (draft itineraries, separate from logged "
                "trips). Returns uuid, name, visibility, trip count and aggregate stats. "
                "Archived plans are included; filter with active_only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"active_only": {"type": "boolean", "description": "Exclude archived plans."}},
                "required": [],
            },
        },
        {
            "name": "get_plan",
            "description": (
                "One plan with its legs (in itinerary order, each with what get_plan_trip "
                "gives but the stops) and its shared costs."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"plan_uuid": {"type": "string"}},
                "required": ["plan_uuid"],
            },
        },
        {
            "name": "get_plan_trip",
            "description": "Everything about one plan leg, in the terms add_plan_trip/update_plan_trip take.",
            "inputSchema": {
                "type": "object",
                "properties": {"plan_uuid": {"type": "string"}, "plan_trip_uid": {"type": "integer"}},
                "required": ["plan_uuid", "plan_trip_uid"],
            },
        },
        {
            "name": "create_plan",
            "description": (
                "Create a new (empty) plan. anchor_date is Day 1 of the itinerary "
                "— relative legs (start_day/end_day) are counted from it. Returns "
                "the new plan_uuid to use with add_plan_trip."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "anchor_date": {"type": "string", "description": "YYYY-MM-DD. Day 1. Defaults to today."},
                    "visibility": {"type": "string", "enum": list(form.VISIBILITIES),
                                   "description": "Who may open the plan's share link (default private)."},
                },
                "required": ["name"],
            },
        },
        {
            "name": "update_plan",
            "description": "Change a plan's name / description / anchor_date / visibility / archived flag. Only supplied fields change.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "anchor_date": {"type": "string", "description": "YYYY-MM-DD."},
                    "visibility": {"type": "string", "enum": list(form.VISIBILITIES)},
                    "archived": {"type": "boolean"},
                },
                "required": ["plan_uuid"],
            },
        },
        {
            "name": "duplicate_plan",
            "description": "Copy a plan with all its legs and costs into a new plan. Returns the new plan_uuid.",
            "inputSchema": {
                "type": "object",
                "properties": {"plan_uuid": {"type": "string"}},
                "required": ["plan_uuid"],
            },
        },
        {
            "name": "delete_plan",
            "description": "Permanently delete a plan and all its legs and costs.",
            "inputSchema": {
                "type": "object",
                "properties": {"plan_uuid": {"type": "string"}},
                "required": ["plan_uuid"],
            },
        },
        {
            "name": "add_plan_trip",
            "description": (
                "Add a leg to a plan, as the plan builder does: same places, routing, "
                "Transitous runs and checks as add_trip. Timing, in one way: relative "
                "(default) start_day/end_day (1 = anchor_date) with optional "
                "start_time+end_time and weekdays; precise start+end; date; or "
                "unknown='future' for an undated leg. No timing = Day 1, untimed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"plan_uuid": {"type": "string"}, **_trip_write_schema(plan=True)},
                "required": ["plan_uuid", "origin"],
            },
        },
        {
            "name": "update_plan_trip",
            "description": (
                "Edit a plan leg, as the plan's trip editor does: only the fields you pass "
                "change. Places/vias/a transitous_trip_id re-route it (a place's `name` "
                "alone renames it). Timing in the leg's own mode keeps what you don't "
                "send (start_time alone keeps the day; start_day alone moves end_day with "
                "it); clear_timing makes it undated."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"plan_uuid": {"type": "string"}, "plan_trip_uid": {"type": "integer"},
                               **_trip_write_schema(plan=True, update=True)},
                "required": ["plan_uuid", "plan_trip_uid"],
            },
        },
        {
            "name": "delete_plan_trip",
            "description": "Remove one leg from a plan.",
            "inputSchema": {
                "type": "object",
                "properties": {"plan_uuid": {"type": "string"}, "plan_trip_uid": {"type": "integer"}},
                "required": ["plan_uuid", "plan_trip_uid"],
            },
        },
        {
            "name": "reorder_plan_trips",
            "description": (
                "Set the manual order of a plan's legs. Pass the full list of leg "
                "uids in the desired order. (Timed legs still sort by time; manual "
                "order decides ties and undated legs.)"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "ordered_uids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["plan_uuid", "ordered_uids"],
            },
        },
        {
            "name": "import_trips_to_plan",
            "description": (
                "Move existing real (logged) trips into a plan by their trip ids. "
                "The originals are removed from the trip log (this is the reverse "
                "of validate_plan). Only your own trips are moved."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "trip_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["plan_uuid", "trip_ids"],
            },
        },
        {
            "name": "validate_plan",
            "description": (
                "Log a plan's legs as real trips (the plan page's \"Log as trips\"): "
                "relative legs are dated from start_date (default: the anchor_date). "
                "Legs already logged are skipped; pass plan_trip_uids to log only some."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD for Day 1."},
                    "plan_trip_uids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["plan_uuid"],
            },
        },
        {
            "name": "add_plan_cost",
            "description": (
                "Add a shared cost (expense) to a plan — e.g. rental car, rail "
                "pass, hotel — counted once in the plan total. Assign legs to it "
                "with assign_trip_cost to show a per-leg share. Returns the cost uid."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "name": {"type": "string"},
                    "price": {"type": "number"},
                    "currency": {"type": "string", "description": "From list_currencies. Default: the user's currency."},
                    "notes": {"type": "string"},
                },
                "required": ["plan_uuid", "name", "price"],
            },
        },
        {
            "name": "update_plan_cost",
            "description": "Update a shared cost's name / price / currency / notes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "cost_uid": {"type": "integer"},
                    "name": {"type": "string"},
                    "price": {"type": "number"},
                    "currency": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["plan_uuid", "cost_uid"],
            },
        },
        {
            "name": "delete_plan_cost",
            "description": "Delete a shared cost from a plan (legs assigned to it are detached).",
            "inputSchema": {
                "type": "object",
                "properties": {"plan_uuid": {"type": "string"}, "cost_uid": {"type": "integer"}},
                "required": ["plan_uuid", "cost_uid"],
            },
        },
        {
            "name": "assign_trip_cost",
            "description": (
                "Assign a plan leg to a shared cost (so the cost's per-leg share "
                "shows on that leg), or detach it by passing cost_uid=null."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "plan_trip_uid": {"type": "integer"},
                    "cost_uid": {"type": ["integer", "null"], "description": "Cost to assign, or null to detach."},
                },
                "required": ["plan_uuid", "plan_trip_uid"],
            },
        },
    ]


# ── serialisation ─────────────────────────────────────────────────────────────


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _km(meters):
    return round(meters / 1000, 1) if meters is not None else None


def _clean(d):
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _trip_summary(r: dict, include_private: bool = True) -> dict:
    """Compact per-trip summary. When include_private is False (friend reads),
    price/currency are dropped so a friend never sees what was paid."""
    out = {
        "id": r["trip_id"],
        "type": r["trip_type"],
        "origin": r["origin_station"],
        "destination": r["destination_station"],
        "start": _iso(r.get("start_datetime")),
        "end": _iso(r.get("end_datetime")),
        "operator": r.get("operator"),
        "line_name": r.get("line_name"),
        "distance_km": _km(r.get("trip_length")),
        "is_project": r.get("is_project"),
        "visibility": r.get("visibility"),
    }
    if include_private:
        out["price"] = r.get("price")
        out["currency"] = r.get("currency")
    return out


def _timing_out(start, end, manual_duration, is_project):
    """A stored trip's dates in add_trip's terms."""
    if start is None:
        out = {"unknown": "future" if is_project else "past"}
    elif start.second == 1:
        out = {"date": start.date().isoformat()}
    else:
        return {"start": start.strftime("%Y-%m-%dT%H:%M"),
                "end": (end or start).strftime("%Y-%m-%dT%H:%M")}
    if manual_duration:
        out["duration_minutes"] = round(manual_duration / 60)
    return out


def _stops_out(waypoints_json):
    """The trip's vias: timetable stops with their local times and platforms, and the
    routing points it was drawn through."""
    try:
        waypoints = json.loads(waypoints_json or "[]")
    except (TypeError, ValueError):
        return []
    out = []
    for wp in waypoints if isinstance(waypoints, list) else []:
        if not isinstance(wp, dict):
            continue
        stop = wp.get("stop") if isinstance(wp.get("stop"), dict) else None
        item = {"name": wp.get("name") or (stop or {}).get("name"), "lat": wp.get("lat"), "lng": wp.get("lng")}
        if wp.get("hard"):
            item["exact"] = True
        if stop:
            tz = ZoneInfo(stop["tz"]) if stop.get("tz") else ZoneInfo("UTC")

            def local(value):
                try:
                    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%Y-%m-%dT%H:%M")
                except (TypeError, ValueError):
                    return None

            item.update({
                "timetable_stop": True,
                "arrival": local(stop.get("arr")), "departure": local(stop.get("dep")),
                "live_arrival": local(stop.get("arr_rt")), "live_departure": local(stop.get("dep_rt")),
                "platform": stop.get("platform"), "live_platform": stop.get("platform_rt"),
            })
        out.append(_clean(item))
    return out


def _trainset_out(value):
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value  # a trainset name
    if isinstance(parsed, list):
        return {"wagons": [{"wagon": u.get("name"), "side": u.get("_side", "L")} for u in parsed if isinstance(u, dict)]}
    return parsed


def _trip_detail(d: dict, owner: bool) -> dict:
    """get_trip: the trip in add_trip's terms."""
    with pg_session() as pg:
        path = form.stored_path(pg, "paths", "trip_id", d["trip_id"])
        tags = [dict(r._mapping) for r in pg.execute(
            "SELECT t.uid AS id, t.name FROM tags_associations a JOIN tags t ON t.uid = a.tag_id"
            " WHERE a.trip_id = :tid ORDER BY t.name", {"tid": d["trip_id"]})] if owner else []
    ends = (path[0], path[-1]) if path else ({}, {})
    out = {
        "id": d["trip_id"],
        "type": d["trip_type"],
        "origin": _clean({"name": d["origin_station"], **ends[0], "station_key": d.get("origin_station_key")}),
        "destination": _clean({"name": d["destination_station"], **ends[1],
                               "station_key": d.get("destination_station_key")}),
        "vias": _stops_out(d.get("waypoints")),
        **_timing_out(d.get("start_datetime"), d.get("end_datetime"),
                      d.get("manual_trip_duration"), d.get("is_project")),
        "utc_start": _iso(d.get("utc_start_datetime")),
        "utc_end": _iso(d.get("utc_end_datetime")),
        "duration_minutes_estimated": round((d.get("estimated_trip_duration") or 0) / 60) or None,
        "distance_km": _km(d.get("trip_length")),
        "countries": json.loads(d["countries"]) if isinstance(d.get("countries"), str) else d.get("countries"),
        "operator": d.get("operator"),
        "line_name": d.get("line_name"),
        "departure_platform": d.get("departure_platform"),
        "arrival_platform": d.get("arrival_platform"),
        "departure_delay_minutes": round(d["departure_delay"] / 60, 1) if d.get("departure_delay") is not None else None,
        "arrival_delay_minutes": round(d["arrival_delay"] / 60, 1) if d.get("arrival_delay") is not None else None,
        "material_type": d.get("material_type"),
        "trainset": _trainset_out(d.get("material_type_advanced")),
        "reg": d.get("reg"),
        "power_type": d.get("power_type"),
        "co2_override": d.get("co2_override"),
        "carbon_kg": round(d["carbon"], 1) if d.get("carbon") is not None else None,
        "visibility": d.get("visibility"),
    }
    if owner:
        out.update({
            "seat": d.get("seat"),
            "seat_car": d.get("seat_car"),
            "notes": d.get("notes"),
            "price": d.get("price"),
            "currency": d.get("currency"),
            "purchase_date": d["purchase_date"].date().isoformat() if d.get("purchase_date") else None,
            "tags": tags,
            "ticket_ids": d.get("ticket_ids"),
            "created": _iso(d.get("created")),
            "last_modified": _iso(d.get("last_modified")),
        })
    return _clean(out) | {"id": d["trip_id"]}


def _plan_summary(p: dict) -> dict:
    return {
        "plan_uuid": p["uuid"],
        "name": p["name"],
        "description": p.get("description"),
        "anchor_date": _iso(p.get("anchor_date")),
        "visibility": p.get("visibility") or "private",
        "archived": p.get("archived"),
        "trip_count": p.get("trip_count"),
        "logged_count": p.get("validated_count"),
        "total_distance_km": round(p["total_distance_km"], 1) if p.get("total_distance_km") is not None else None,
    }


def _plan_trip_detail(r: dict, stops=True) -> dict:
    """A plan leg in add_plan_trip's terms."""
    path = [{"lat": c[0], "lng": c[1]} for c in form.geom_geojson_to_coords(r["geojson"])] \
        if r.get("geojson") else []
    ends = (path[0], path[-1]) if path else ({}, {})
    mode = r.get("timing_mode")
    if mode == "relative":
        timing = {"start_day": r.get("start_day"), "end_day": r.get("end_day"),
                  "start_time": r["start_time"].strftime("%H:%M") if r.get("start_time") else None,
                  "end_time": r["end_time"].strftime("%H:%M") if r.get("end_time") else None,
                  "weekdays": form.weekdays_list(r.get("weekdays"))}
        if r.get("manual_trip_duration"):
            timing["duration_minutes"] = round(r["manual_trip_duration"] / 60)
    elif mode in ("preciseDates", "onlyDate"):
        timing = _timing_out(r.get("start_datetime"), r.get("end_datetime"), r.get("manual_trip_duration"), True)
    else:
        timing = {"unknown": "future"}
    out = {
        "uid": r["uid"],
        "type": r["trip_type"],
        "origin": _clean({"name": r["origin_station"], **ends[0]}),
        "destination": _clean({"name": r["destination_station"], **ends[1]}),
        **timing,
        "duration_minutes_estimated": round((r.get("estimated_trip_duration") or 0) / 60) or None,
        "distance_km": _km(r.get("trip_length")),
        "operator": r.get("operator"),
        "line_name": r.get("line_name"),
        "material_type": r.get("material_type"),
        "trainset": _trainset_out(r.get("material_type_advanced")),
        "reg": r.get("reg"),
        "seat": r.get("seat"),
        "notes": r.get("notes"),
        "price": r.get("price"),
        "currency": r.get("currency"),
        "purchase_date": _iso(r["purchase_date"].date()) if r.get("purchase_date") else None,
        "booked": r.get("booked"),
        "power_type": r.get("power_type"),
        "co2_override": r.get("co2_override"),
        "visibility": r.get("visibility"),
        "cost_uid": r.get("cost_id"),
        "logged_trip_id": r.get("validated_trip_id"),
        "sort_order": r.get("sort_order"),
    }
    if stops:
        out["vias"] = _stops_out(r.get("waypoints"))
    return _clean(out) | {"uid": r["uid"]}


def _plan_cost_summary(r: dict) -> dict:
    return {
        "uid": r["uid"],
        "name": r["name"],
        "price": r.get("price"),
        "currency": r.get("currency"),
        "notes": r.get("notes"),
        "leg_count": r.get("leg_count"),
    }


# ── links ─────────────────────────────────────────────────────────────────────


def _trip_links(username, trip_ids) -> dict:
    """Where the user sees trips: on a map, and in their trips list filtered to them
    (`search=id:…`, as the timeline links them) — the trips list, or the projects list
    for the future ones, following the list's own split (get_dynamic_user_trips)."""
    ids = sorted({int(t) for t in trip_ids})
    if not ids:
        return {}
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT trip_id, is_project OR (utc_start_datetime IS NOT NULL AND NOW() <= utc_start_datetime)"
            " AS upcoming FROM trips WHERE trip_id = ANY(:ids)", {"ids": ids}).fetchall()
    lists = {"trips": [r[0] for r in rows if not r[1]], "projects": [r[0] for r in rows if r[1]]}
    links = {"map": external_url("public_trip", tripIds=",".join(map(str, ids)))}
    for time, listed in lists.items():
        if listed:
            links["trips_list" if time == "trips" else "upcoming_trips_list"] = external_url(
                "dynamic_trips", username=username, time=time,
                search="id:" + "|".join(map(str, listed)))
    return links


def _plan_links(username, plan_uuid) -> dict:
    return {"plan": external_url("plan_view", username=username, plan_uuid=plan_uuid),
            "map": external_url("public_plan", plan_uuid=plan_uuid)}


def _straight_line(remarks):
    return form.STRAIGHT_LINE_REMARK in remarks or None


# ── lookups ───────────────────────────────────────────────────────────────────


def _search_stations(args: dict) -> list[dict]:
    """The trip form's station suggestions (station_autocomplete, or the airport search
    for flights), as the places add_trip takes."""
    trip_type = form.check_type(args.get("type") or "train")
    query = (args.get("query") or "").strip()
    limit = max(1, min(int(args.get("limit") or 8), 20))
    if not query:
        return []

    if trip_type == "air":
        with pg_session() as pg:
            rows = pg.execute(get_airports_query(), {"searchPattern": "%" + query + "%"}).fetchall()
        return [{
            "name": f"{form.get_flag_emoji(a['iso_country'])} {a['name']} ({a['iata']})",
            "iata": a["iata"], "lat": a["latitude"], "lng": a["longitude"], "country": a["iso_country"],
        } for a in (dict(r._mapping) for r in rows[:limit]) if a.get("iata")]

    params = {"q": [query], "limit": [str(limit)], **STATION_SEARCH_PARAMS.get(trip_type, {})}
    if args.get("near_lat") is not None and args.get("near_lng") is not None:
        params["lat"], params["lon"] = [str(float(args["near_lat"]))], [str(float(args["near_lng"]))]
    payload, error = form.site().station_autocomplete(params)
    if payload is None:
        raise ValueError("Station search is temporarily unavailable.")
    out = []
    for feature in payload.get("features", [])[:limit]:
        props = feature.get("properties", {})
        lng, lat = (feature.get("geometry", {}).get("coordinates") or [None, None])[:2]
        if lat is None or not props.get("name"):
            continue
        place = [props.get(k) for k in ("street", "locality", "district", "city", "state")]
        out.append(_clean({
            # The label the form saves ([coords, label]): the homonym letter is for the list only.
            "name": f"{form.get_flag_emoji(props['countrycode'])} {props['name']}" if props.get("countrycode")
                    else props["name"],
            "lat": lat,
            "lng": lng,
            "station_key": props.get("station_key") if props.get("source") == "quai" else None,
            "country": props.get("countrycode"),
            "where": ", ".join(dict.fromkeys(p for p in place if p)) or None,
            "kind": props.get("osm_value") or props.get("mode"),
            "tracks": [t["ref"] for t in props.get("tracks") or [] if t.get("ref")] or None,
        }))
    return out


def _search_departures(args: dict) -> dict:
    """search_departures: the new trip form's timetable list, trimmed for an LLM."""
    trip_type = args.get("type", "train")
    if trip_type not in TRANSIT_MODES:
        raise ValueError(f"No timetables for trip type {trip_type!r}; use one of {sorted(TRANSIT_MODES)}.")
    try:
        query = {
            "type": trip_type,
            "from": f"{float(args['origin_lat'])},{float(args['origin_lng'])}",
            "to": f"{float(args['destination_lat'])},{float(args['destination_lng'])}",
            "date": datetime.strptime(args.get("date") or "", "%Y-%m-%d").strftime("%Y-%m-%d"),
        }
        if args.get("time"):
            query["time"] = datetime.strptime(args["time"], "%H:%M").strftime("%H:%M")
        payload, _ = departures_payload(query)
    except (KeyError, TypeError) as e:
        raise ValueError(f"Missing or invalid coordinates: {e}")
    except (ValueError, HTTPException):
        raise ValueError("Invalid search: check the coordinates, the date (YYYY-MM-DD) and time (HH:MM).")
    if payload is None or payload.get("error") == "unavailable":
        raise ValueError("The timetable service (Transitous) is unavailable right now. Try again later.")
    if payload.get("error") == "rate_limited":
        raise ValueError("The timetable service (Transitous) is rate-limiting; wait a minute and retry.")
    if payload.get("outside_timetable"):
        return {"outside_timetable": True, "departures": []}

    limit = max(1, min(int(args.get("limit") or 15), 60))
    departures = []
    for d in payload["departures"][:limit]:
        stops = [
            " ".join(filter(None, [stop[4] or stop[5], stop[2], f"(pl. {stop[6]})" if stop[6] else None]))
            for stop in d["stops"]
        ]
        departures.append(_clean({
            "trip_id": d["trip_id"],
            "departure": d["departure"],
            "arrival": d["arrival"],
            "duration_min": round(d["duration"] / 60) if d.get("duration") else None,
            "line": d["line"] or None,
            "headsign": d["headsign"] or None,
            "operator": d.get("operator") or None,
            "mode": d["mode"],
            "replacement_bus": d["replacement_bus"] or None,
            "from_stop": d["from_name"],
            "to_stop": d["to_name"],
            "from_platform": d["from_platform"],
            "to_platform": d["to_platform"],
            "departure_delay_min": d["departure_delay"] or None,
            "arrival_delay_min": d["arrival_delay"] or None,
            "intermediate_stops": stops,
        }))
    return {"departures": departures, "more": len(payload["departures"]) > limit}


def _trip_types(user) -> list[dict]:
    out = []
    for t in form.form_types():
        out.append(_clean({
            "type": t,
            "default_visibility": form.get_default_trip_visibility(t),
            "power_types": list(form.POWER_TYPES.get(t, ())),
            "trainset": t in form.TRAINSET_TYPES or None,
            "co2_override": t in form.CO2_OVERRIDE_TYPES or None,
            "timetable_runs": t in TRANSIT_MODES or None,
            "vias": (t not in form.AIR_TYPES and t not in form.SINGLE_POINT_TYPES) or None,
            "single_place": t in form.SINGLE_POINT_TYPES or None,
            "places_by": "airport iata" if t == "air" else None,
        }))
    return out


def _search_operators(user, args) -> list[dict]:
    trip_type = form.check_type(args.get("type") or "train")
    query = (args.get("query") or "").strip().lower()
    limit = max(1, min(int(args.get("limit") or 20), 100))
    logo_type = trip_type if trip_type in ("accommodation", "poi", "car") else "operator"
    with pg_session() as pg:
        own = [str(r["operator"]).strip() for r in pg.execute(get_operators_query(), {"user_id": user.uid})]
    known = form.site().listOperatorsLogos(logo_type)
    names = [n for n in dict.fromkeys(own + list(known)) if n and n.strip()]
    if query:
        names = [n for n in names if query in n.lower()]
        names.sort(key=lambda n: (n.lower().find(query), len(n)))
    return [_clean({"operator": n, "used_before": n in own or None, "has_logo": n in known or None})
            for n in names[:limit]]


def _search_material_types(user, args) -> list:
    trip_type = form.check_type(args.get("type") or "train")
    query = (args.get("query") or "").strip().lower()
    limit = max(1, min(int(args.get("limit") or 30), 200))
    with pg_session() as pg:
        if trip_type == "air":
            rows = [dict(r._mapping) for r in pg.execute("SELECT iata, manufacturer, model FROM airliners")]
            items = [{"material_type": r["iata"], "name": f"{r['manufacturer']} {r['model']}"} for r in rows
                     if query in f"{r['iata']} {r['manufacturer']} {r['model']}".lower()]
        else:
            values = [str(r["material_type"]).strip() for r in pg.execute(
                get_material_types_query(), {"trip_type": trip_type, "user_id": user.uid}) if r["material_type"]]
            items = [{"material_type": v} for v in sorted(set(values)) if query in v.lower()]
    return items[:limit]


def _list_trainsets(user, args) -> list[dict]:
    query = (args.get("query") or "").strip().lower()
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT name, is_admin, units_json FROM trainsets WHERE is_admin OR username = :u"
            " ORDER BY is_admin, name", {"u": user.username}).fetchall()
        labels = {}
        out = []
        for r in rows:
            if query and query not in r["name"].lower():
                continue
            units = json.loads(r["units_json"] or "[]")
            names = [u.get("name") for u in units if isinstance(u, dict)]
            missing = [n for n in names if n not in labels]
            if missing:
                labels.update({w["name"]: w["label"] for w in pg.execute(
                    "SELECT name, label FROM wagons WHERE name = ANY(:n)", {"n": missing})})
            out.append({"name": r["name"], "public": bool(r["is_admin"]), "cars": len(units),
                        "composition": [labels.get(n, n) for n in names]})
    return out


def _search_wagons(args) -> list[dict]:
    terms = [t for t in (args.get("query") or "").split() if t]
    if not terms:
        return []
    limit = max(1, min(int(args.get("limit") or 20), 100))
    fields = ["label", "category", "subcategory", "notes", "era", "source", "name"]
    params = {"limit": limit}
    where = []
    for i, term in enumerate(terms):
        params[f"t{i}"] = f"%{term}%"
        where.append("(" + " OR ".join(f"{f} ILIKE :t{i}" for f in fields) + f" OR gauge::text ILIKE :t{i})")
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT name, label, category, subcategory, era, image_type FROM wagons WHERE "
            + " AND ".join(where) + " ORDER BY label LIMIT :limit", params).fetchall()
    return [_clean({"wagon": r["name"], "label": r["label"], "category": r["category"],
                    "subcategory": r["subcategory"], "era": r["era"],
                    "two_sided": r["image_type"] != "plain" or None}) for r in rows]


def _list_tags(user) -> list[dict]:
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT t.uid, t.name, t.username FROM tags t WHERE t.username = :u"
            " OR EXISTS (SELECT 1 FROM tag_members m WHERE m.tag_id = t.uid AND m.username = :u"
            "            AND m.status = 'accepted')"
            " ORDER BY t.name", {"u": user.username}).fetchall()
    return [_clean({"id": r["uid"], "name": r["name"],
                    "shared_by": r["username"] if r["username"] != user.username else None}) for r in rows]


def _list_tickets(user) -> list[dict]:
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT uid, name, price, currency, operator, valid_from, valid_until, active, product"
            " FROM tickets WHERE username = :u ORDER BY COALESCE(valid_from, purchasing_date) DESC NULLS LAST",
            {"u": user.username}).fetchall()
    return [_clean({"id": r["uid"], "name": r["name"], "price": r["price"], "currency": r["currency"],
                    "operator": r["operator"], "product": r["product"], "active": r["active"],
                    "valid_from": _iso(r["valid_from"]), "valid_until": _iso(r["valid_until"])}) for r in rows]


# ── writes ────────────────────────────────────────────────────────────────────


def _add_trip(user, args) -> dict:
    new_trip, path, remarks = form.new_trip_form(user, args)
    trip = form.site().saveTripToDb(username=user.username, newTrip=new_trip, newPath=path,
                                    trip_type=new_trip["type"])
    return _clean({"ok": True, "id": trip.trip_id, "origin": trip.origin_station,
                   "destination": trip.destination_station, "start": _iso(trip.start_datetime),
                   "end": _iso(trip.end_datetime), "distance_km": _km(trip.trip_length),
                   "straight_line": _straight_line(remarks), "remarks": remarks,
                   "links": _trip_links(user.username, [trip.trip_id])})


def _update_trip(user, args) -> dict:
    trip_id = _owned_trip_id(args.get("trip_id"), user.uid)
    changes = {k: v for k, v in args.items() if k != "trip_id"}
    if not changes:
        raise ValueError("Nothing to update.")
    new_type, patch, tag_ids, remarks = form.trip_patch(user, trip_id, changes)
    if new_type:
        update_trip_type(trip_id, TripTypes(new_type))
    if patch:
        form.site().apply_trip_patch(trip_id, patch)
    if tag_ids is not None:
        with pg_session() as pg:
            pg.execute("DELETE FROM tags_associations WHERE trip_id = :tid", {"tid": trip_id})
            for tag_id in tag_ids:
                pg.execute("INSERT INTO tags_associations (tag_id, trip_id) VALUES (:t, :tid)"
                           " ON CONFLICT DO NOTHING", {"t": tag_id, "tid": trip_id})
    with pg_session() as pg:
        row = pg.execute("SELECT * FROM trips WHERE trip_id = :tid", {"tid": trip_id}).fetchone()
    return _clean({"ok": True, "trip": _trip_detail(dict(row._mapping), True),
                   "straight_line": _straight_line(remarks), "remarks": remarks,
                   "links": _trip_links(user.username, [trip_id])})


def _cost_fields(user, args, current=None):
    current = current or {}
    name = form.text(args, "name", 200) if "name" in args else current.get("name")
    if not name:
        raise ValueError("A cost needs a name.")
    price = form.number(args, "price", 0) if "price" in args else current.get("price")
    if price is None:
        raise ValueError("A cost needs a price (≥ 0).")
    currency = args.get("currency") or current.get("currency") or user.user_currency or "EUR"
    return {
        "name": name, "price": price,
        "currency": form.check_currency(user.username, currency),
        "notes": form.text(args, "notes", 5000) if "notes" in args else current.get("notes"),
    }


def _plan_fields(args):
    out = {}
    if "name" in args:
        out["name"] = form.text(args, "name", 200)
        if not out["name"]:
            raise ValueError("A plan needs a name.")
    if "description" in args:
        out["description"] = form.text(args, "description", 5000)
    if args.get("anchor_date"):
        out["anchor_date"] = form.iso_date(args["anchor_date"], "anchor_date")
    if "visibility" in args:
        out["visibility"] = form.check_visibility(args["visibility"])
    return out


def _call_tool(name: str, args: dict, user) -> str:
    uid = user.uid
    args = args or {}

    def out(value):
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)

    # ── lookups ─────────────────────────────────────────────────────────────
    if name == "search_stations":
        return out(_search_stations(args))
    if name == "search_departures":
        return out(_search_departures(args))
    if name == "list_trip_types":
        return out(_trip_types(user))
    if name == "list_currencies":
        return out([c["currency"] for c in get_available_currencies(user.username)])
    if name == "search_operators":
        return out(_search_operators(user, args))
    if name == "search_material_types":
        return out(_search_material_types(user, args))
    if name == "list_trainsets":
        return out(_list_trainsets(user, args))
    if name == "search_wagons":
        return out(_search_wagons(args))
    if name == "list_tags":
        return out(_list_tags(user))
    if name == "list_tickets":
        return out(_list_tickets(user))
    if name == "list_friends":
        rows = Friendship.query.filter(Friendship.user_id == uid, Friendship.accepted.isnot(None)).all()
        friend_uids = [f.friend_id for f in rows]
        friends = User.query.filter(User.uid.in_(friend_uids)).all() if friend_uids else []
        return out(sorted(u.username for u in friends))

    # ── trips ───────────────────────────────────────────────────────────────
    if name == "list_trips":
        limit = max(1, min(int(args.get("limit") or 50), 500))
        # Whose trips? Default own; an explicit username reads a friend's, read-only.
        target_username = (args.get("username") or "").strip()
        own = not target_username or target_username == user.username
        if own:
            target_uid = uid
        else:
            target = User.query.filter_by(username=target_username).first()
            if target is None or not _are_friends(uid, target.uid):
                # Don't disclose whether the user exists.
                raise ValueError("No such friend, or you are not friends with them.")
            target_uid = target.uid

        clauses = ["user_id = :target_uid"]
        params = {"target_uid": target_uid, "limit": limit}
        if not own:
            # FRIEND_VISIBLE is a fixed tuple of trusted literals (no user input).
            allowed = ", ".join(f"'{v}'" for v in FRIEND_VISIBLE)
            clauses.append(f"visibility IN ({allowed})")
        if args.get("trip_type"):
            clauses.append("trip_type = :trip_type")
            params["trip_type"] = args["trip_type"]
        if args.get("upcoming_only"):
            clauses.append("(is_project OR COALESCE(utc_start_datetime, start_datetime) >= NOW())")
        if args.get("from_date"):
            clauses.append("start_datetime >= :from_date")
            params["from_date"] = form.iso_date(args["from_date"], "from_date")
        if args.get("to_date"):
            clauses.append("start_datetime < CAST(:to_date AS date) + 1")
            params["to_date"] = form.iso_date(args["to_date"], "to_date")
        with pg_session() as pg:
            rows = pg.execute(
                f"""
                SELECT trip_id, trip_type, origin_station, destination_station,
                       start_datetime, end_datetime, operator, line_name,
                       trip_length, price, currency, is_project, visibility
                FROM trips
                WHERE {" AND ".join(clauses)}
                ORDER BY (is_project AND utc_start_datetime IS NULL) DESC,
                         COALESCE(utc_start_datetime, start_datetime) DESC NULLS LAST,
                         trip_id DESC
                LIMIT :limit
                """,
                params,
            ).fetchall()
        return out([_trip_summary(dict(r._mapping), include_private=own) for r in rows])

    if name == "get_trip":
        try:
            trip_id = int(args.get("trip_id"))
        except (TypeError, ValueError):
            raise ValueError("trip_id must be a number.")
        with pg_session() as pg:
            row = pg.execute("SELECT * FROM trips WHERE trip_id = :tid", {"tid": trip_id}).fetchone()
        if row is None:
            raise ValueError("Trip not found.")
        d = dict(row._mapping)
        owner = d["user_id"] == uid
        if not owner:
            # A friend may read only public/friends trips, and only if friends.
            if d.get("visibility") not in FRIEND_VISIBLE or not _are_friends(uid, d["user_id"]):
                raise ValueError("Trip not found.")
        detail = _trip_detail(d, owner)
        if owner:
            detail["links"] = _trip_links(user.username, [trip_id])
        return out(detail)

    if name == "add_trip":
        return out(_add_trip(user, args))
    if name == "update_trip":
        return out(_update_trip(user, args))
    if name == "delete_trip":
        trip_id = _owned_trip_id(args.get("trip_id"), uid)
        _delete_trip(trip_id, user.username)
        return out({"ok": True})

    # ── plans ───────────────────────────────────────────────────────────────
    if name == "list_plans":
        with pg_session() as pg:
            rows = pg.execute(get_user_plans_query(), {"user_id": uid}).fetchall()
        plans = [_plan_summary(dict(r._mapping)) for r in rows]
        if args.get("active_only"):
            plans = [p for p in plans if not p["archived"]]
        return out(plans)

    if name == "get_plan":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        with pg_session() as pg:
            trips = pg.execute(get_plan_trips_query(), {"plan_id": plan["uid"]}).fetchall()
            costs = pg.execute(get_plan_costs_query(), {"plan_id": plan["uid"]}).fetchall()
        legs = [dict(r._mapping) for r in trips]
        return out({
            **_plan_summary({
                **plan, "trip_count": len(legs),
                "validated_count": sum(1 for r in legs if r.get("validated_trip_id")),
                "total_distance_km": sum(r.get("trip_length") or 0 for r in legs) / 1000,
            }),
            "trips": [_plan_trip_detail(r, stops=False) for r in legs],
            "costs": [_plan_cost_summary(dict(r._mapping)) for r in costs],
            "links": _plan_links(user.username, plan["uuid"]),
        })

    if name == "get_plan_trip":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        return out(_plan_trip_detail(_owned_plan_trip(plan, args.get("plan_trip_uid"))))

    if name == "create_plan":
        fields = _plan_fields(args)
        if not fields.get("name"):
            raise ValueError("A plan needs a name.")
        now = datetime.now()
        plan_uuid = str(_uuid.uuid4())
        with pg_session() as pg:
            pg.execute(insert_plan_query(), {
                "uuid": plan_uuid, "user_id": uid, "name": fields["name"],
                "description": fields.get("description"),
                "anchor_date": fields.get("anchor_date") or now.date(),
                "created": now, "last_modified": now,
            })
            if fields.get("visibility"):
                row = pg.execute(get_plan_query(), {"uuid": plan_uuid}).fetchone()
                pg.execute(update_plan_visibility_query(), {
                    "uid": row._mapping["uid"], "user_id": uid,
                    "visibility": fields["visibility"], "last_modified": now})
        return out({"ok": True, "plan_uuid": plan_uuid, "links": _plan_links(user.username, plan_uuid)})

    if name == "update_plan":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        fields = _plan_fields(args)
        now = datetime.now()
        with pg_session() as pg:
            if fields.keys() & {"name", "description", "anchor_date"}:
                pg.execute(update_plan_query(), {
                    "uid": plan["uid"], "user_id": uid,
                    "name": fields.get("name", plan["name"]),
                    "description": fields["description"] if "description" in fields else plan.get("description"),
                    "anchor_date": fields.get("anchor_date") or plan["anchor_date"],
                    "last_modified": now,
                })
            if "visibility" in fields:
                pg.execute(update_plan_visibility_query(), {
                    "uid": plan["uid"], "user_id": uid, "visibility": fields["visibility"],
                    "last_modified": now})
            if "archived" in args:
                pg.execute(archive_plan_query(), {
                    "uid": plan["uid"], "user_id": uid,
                    "archived": bool(args["archived"]), "last_modified": now,
                })
        return out({"ok": True})

    if name == "duplicate_plan":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        suffix = form.site().lang.get(user.lang or "en", {}).get("copySuffix", "(copy)")
        new_uuid = _duplicate_plan(plan["uuid"], uid, name=f"{plan['name']} {suffix}")
        if new_uuid is None:
            raise ValueError("Plan not found.")
        return out({"ok": True, "plan_uuid": new_uuid, "links": _plan_links(user.username, new_uuid)})

    if name == "delete_plan":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        _delete_plan(plan["uid"], uid)
        return out({"ok": True})

    if name == "add_plan_trip":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        new_trip, path, remarks = form.new_trip_form(user, args, plan=True)
        pt = form.site().savePlanTripToDb(user.username, new_trip, path, plan, trip_type=new_trip["type"])
        return out(_clean({"ok": True, "plan_trip_uid": pt.uid, "origin": pt.origin_station,
                           "destination": pt.destination_station,
                           "straight_line": _straight_line(remarks), "remarks": remarks,
                           "links": _plan_links(user.username, plan["uuid"])}))

    if name == "update_plan_trip":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        pt = _owned_plan_trip(plan, args.get("plan_trip_uid"))
        changes = {k: v for k, v in args.items() if k not in ("plan_uuid", "plan_trip_uid")}
        if not changes:
            raise ValueError("Nothing to update.")
        edit_form, remarks = form.plan_trip_edit_form(user, pt, changes)
        form.site().save_plan_trip_edit(plan, pt, edit_form)
        return out(_clean({"ok": True, "plan_trip": _plan_trip_detail(_owned_plan_trip(plan, pt["uid"])),
                           "straight_line": _straight_line(remarks), "remarks": remarks,
                           "links": _plan_links(user.username, plan["uuid"])}))

    if name == "delete_plan_trip":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        _owned_plan_trip(plan, args.get("plan_trip_uid"))
        _delete_plan_trip(int(args["plan_trip_uid"]), uid)
        return out({"ok": True})

    if name == "reorder_plan_trips":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        order = [int(x) for x in (args.get("ordered_uids") or [])]
        with pg_session() as pg:
            legs = {r[0] for r in pg.execute("SELECT uid FROM plan_trips WHERE plan_id = :p", {"p": plan["uid"]})}
        if set(order) != legs or len(order) != len(legs):
            raise ValueError(f"ordered_uids must list each of the plan's legs once: {sorted(legs)}.")
        with pg_session() as pg:
            for i, leg_uid in enumerate(order):
                pg.execute(reorder_plan_trip_query(), {
                    "uid": leg_uid, "plan_id": plan["uid"], "user_id": uid, "sort_order": i})
        return out({"ok": True})

    if name == "import_trips_to_plan":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        ids = [int(x) for x in (args.get("trip_ids") or [])]
        moved = import_trips_to_plan(plan, ids) if ids else []
        return out({"ok": True, "moved_trip_ids": moved, "links": _plan_links(user.username, plan["uuid"])})

    if name == "validate_plan":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        start = form.iso_date(args["start_date"], "start_date") if args.get("start_date") else plan["anchor_date"]
        uids = args.get("plan_trip_uids")
        tag_uuid = _validate_plan(plan, start, plan_trip_uids=[int(x) for x in uids] if uids else None)
        with pg_session() as pg:
            logged = [dict(r._mapping) for r in pg.execute(
                "SELECT uid AS plan_trip_uid, validated_trip_id AS trip_id FROM plan_trips"
                " WHERE plan_id = :p AND validated_trip_id IS NOT NULL ORDER BY sort_order", {"p": plan["uid"]})]
        return out({"ok": True, "tag_uuid": tag_uuid, "logged": logged, "links": {
            **_trip_links(user.username, [r["trip_id"] for r in logged]),
            "plan": _plan_links(user.username, plan["uuid"])["plan"]}})

    if name == "add_plan_cost":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        fields = _cost_fields(user, args)
        now = datetime.now()
        with pg_session() as pg:
            cost_uid = pg.execute(insert_plan_cost_query(), {
                "plan_id": plan["uid"], **fields, "created": now, "last_modified": now,
            }).fetchone()[0]
        return out({"ok": True, "cost_uid": cost_uid})

    if name == "update_plan_cost":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        with pg_session() as pg:
            row = pg.execute(
                "SELECT name, price, currency, notes FROM plan_costs WHERE uid = :uid AND plan_id = :pid",
                {"uid": args.get("cost_uid"), "pid": plan["uid"]},
            ).fetchone()
            if row is None:
                raise ValueError("Cost not found.")
            fields = _cost_fields(user, args, dict(row._mapping))
            pg.execute(update_plan_cost_query(), {
                "uid": args["cost_uid"], "plan_id": plan["uid"], **fields,
                "last_modified": datetime.now(),
            })
        return out({"ok": True})

    if name == "delete_plan_cost":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        with pg_session() as pg:
            pg.execute(delete_plan_cost_query(), {"uid": args.get("cost_uid"), "plan_id": plan["uid"]})
        return out({"ok": True})

    if name == "assign_trip_cost":
        plan = _owned_plan(args.get("plan_uuid"), uid)
        cost_uid = args.get("cost_uid")
        cost_uid = int(cost_uid) if cost_uid not in (None, "", "none") else None
        with pg_session() as pg:
            if cost_uid is not None:
                # The cost must belong to this plan (don't link a leg to a foreign cost).
                exists = pg.execute(
                    "SELECT 1 FROM plan_costs WHERE uid = :c AND plan_id = :p",
                    {"c": cost_uid, "p": plan["uid"]},
                ).fetchone()
                if exists is None:
                    raise ValueError("Cost not found.")
            res = pg.execute(set_plan_trip_cost_query(), {
                "uid": args.get("plan_trip_uid"), "plan_id": plan["uid"],
                "cost_id": cost_uid, "last_modified": datetime.now(),
            })
            if res.rowcount == 0:
                raise ValueError("Plan leg not found.")
        return out({"ok": True})

    raise ValueError(f"Unknown tool: {name}")


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────

PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
# A JSON-RPC batch is that many tool calls in one request.
MAX_BATCH = 20

# What each write does to existing data, for the client's approval prompts (MCP tool
# annotations): destructive = deletes, overwrites, or moves trips out of the log.
DESTRUCTIVE_TOOLS = {
    "delete_trip", "update_trip", "delete_plan", "update_plan", "delete_plan_trip",
    "update_plan_trip", "delete_plan_cost", "update_plan_cost", "import_trips_to_plan",
}
IDEMPOTENT_TOOLS = {"reorder_plan_trips", "assign_trip_cost", "update_plan", "update_plan_cost"}


def _annotations(name):
    if name.startswith(("search_", "list_", "get_")):
        return {"readOnlyHint": True, "openWorldHint": name == "search_departures"}
    return {
        "readOnlyHint": False,
        "destructiveHint": name in DESTRUCTIVE_TOOLS,
        "idempotentHint": name in IDEMPOTENT_TOOLS,
        "openWorldHint": False,
    }


SERVER_INSTRUCTIONS = (
    "Trainlog logs a user's trips and plans; these tools do what its website does, with "
    "the same checks. Take places from search_stations. After creating or changing trips "
    "or plans, give the user the result's `links` (map, and the trips list filtered to "
    "the trips touched, or the plan page). When a result has `straight_line: true`, the "
    "router found no route and the trip was saved as straight lines: say so clearly, "
    "and that it can be redrawn on the trip's edit page. Relay any `remarks`."
)



def _handle_rpc(msg: dict, user) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        requested = (msg.get("params") or {}).get("protocolVersion")
        return ok({
            # Tool annotations came with 2025-03-26; older clients get what they asked for.
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Trainlog", "version": "2.0"},
            "instructions": SERVER_INSTRUCTIONS,
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": [{**t, "annotations": _annotations(t["name"])} for t in _build_tools()]})

    if method == "tools/call":
        params = msg.get("params", {})
        try:
            with _acting_as(user):
                text = _call_tool(params.get("name", ""), params.get("arguments") or {}, user)
            return ok({"content": [{"type": "text", "text": text}]})
        except ValueError as e:
            return ok({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        except HTTPException as e:
            # The site's own checks abort (a 404 for a trip that isn't the user's…).
            return ok({"content": [{"type": "text", "text": f"Error: {e.description}"}], "isError": True})
        except Exception:
            # Logged, not returned: the message may carry SQL or internals.
            logger.exception("MCP tool error")
            return err(-32603, "Internal error.")

    if msg_id is not None:
        return err(-32601, f"Method not found: {method}")
    return None


# ── Flask route ───────────────────────────────────────────────────────────────


@blueprint.route("", methods=["POST"])
def handle():
    user = _auth()
    if user is None:
        return Response("Unauthorized", status=401)
    if not user.premium:
        return Response("MCP access requires a premium account.", status=403)

    msg = request.get_json(silent=True)
    if not msg:
        return Response("Bad request", status=400)

    if isinstance(msg, list):  # JSON-RPC batch
        if len(msg) > MAX_BATCH:
            return Response(f"At most {MAX_BATCH} messages per batch.", status=413)
        responses = [r for r in (_handle_rpc(m, user) for m in msg) if r is not None]
        return Response(json.dumps(responses), status=200, content_type="application/json")

    response = _handle_rpc(msg, user)
    if response is None:
        return Response(status=202)
    return Response(json.dumps(response), status=200, content_type="application/json")
