"""src/api/mcp.py — MCP server as a Flask Blueprint (Streamable HTTP transport).

Single POST endpoint — no persistent connections, WSGI-friendly. Lets an external
AI manage a user's trips. This is the successor to src/ai.py + src/api/ai.py:
instead of Trainlog calling an LLM internally to parse trips, an external LLM now
drives Trainlog through MCP tools, reusing the same geocoding/routing/trip-creation
pipeline (enrich_parsed_trip / create_trip_from_parsed).

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
import re

from flask import Blueprint, Response, request

import uuid as _uuid
from datetime import datetime

from py.utils import getCountriesFromPath, getCountryFromCoordinates
from src.ai import create_trip_from_parsed, enrich_parsed_trip
from src.consts import TripTypes
from src.paths import geom_geojson_to_coords
from src.photon import photonRequest
from src.pg import pg_session
from src.sql.stations import get_airports_query
from src.plans.create_from_parsed import create_plan_trip_from_parsed
from src.plans.delete_plan import delete_plan as _delete_plan
from src.plans.delete_plan import delete_plan_trip as _delete_plan_trip
from src.plans.import_trips import import_trips_to_plan
from src.plans.plan_trip import PlanTrip
from src.plans.process_plan_dates import process_plan_dates
from src.plans.update_plan_trip_full import update_plan_trip_full
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
)
from src.trips.delete_trip import delete_trip as _delete_trip
from src.users import Friendship, User

# Trips a friend is allowed to read. Mirrors the friend-facing read path
# (dashboard_friends): explicit public/friends only — NULL visibility is NOT
# exposed to friends.
FRIEND_VISIBLE = ("public", "friends")

logger = logging.getLogger(__name__)

blueprint = Blueprint("mcp", __name__, url_prefix="/mcp")

TRIP_TYPE_VALUES = [t.value for t in TripTypes]

# Per-trip-type OSM tag filters for the station search, mirroring the URLs map in
# templates/new.html. A type absent from this map (car, walk, cycle, scooter,
# other) searches all places with no tag filter. 'air' is handled separately via
# the airport database, not Photon.
STATION_SEARCH_OSM_TAGS = {
    "bus": ["amenity:bus_station", "highway:bus_stop"],
    "train": ["railway:halt", "railway:station"],
    "tram": ["railway:tram_stop", "railway:station", "railway:halt"],
    "metro": ["railway:station", "railway:subway_entrance"],
    "funicular": ["railway:halt", "railway:station"],
    "rail": ["railway:halt", "railway:station"],
    "ferry": ["amenity:ferry_terminal"],
    "helicopter": ["aeroway:helipad", "aeroway:heliport", "aeroway:aerodrome"],
    "accomodation": [
        "tourism:alpine_hut", "tourism:apartment", "tourism:chalet",
        "tourism:guest_house", "tourism:hostel", "tourism:hotel",
        "tourism:motel", "tourism:wilderness_hut",
    ],
    "poi": [
        "tourism", "!tourism:alpine_hut", "!tourism:apartment", "!tourism:chalet",
        "!tourism:guest_house", "!tourism:hostel", "!tourism:hotel",
        "!tourism:motel", "!tourism:wilderness_hut",
    ],
    "restaurant": [
        "amenity:restaurant", "amenity:pub", "amenity:biergarten",
        "amenity:cafe", "amenity:bar",
    ],
    "aerialway": ["aerialway:station"],
    "ski": ["aerialway:station"],
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
    with pg_session() as pg:
        row = pg.execute(get_plan_query(), {"uuid": plan_uuid}).fetchone()
    if row is None:
        raise ValueError("Plan not found.")
    plan = dict(row._mapping)
    if plan["user_id"] != uid:
        raise ValueError("Plan not found.")
    return plan


def _owned_plan_trip(plan: dict, plan_trip_uid) -> dict:
    """Fetch one leg (with its path as GeoJSON) of an already-ownership-checked plan.
    ValueError if it isn't in that plan."""
    with pg_session() as pg:
        row = pg.execute(
            "SELECT *, ST_AsGeoJSON(geom) AS geojson FROM plan_trips"
            " WHERE uid = :uid AND plan_id = :pid",
            {"uid": plan_trip_uid, "pid": plan["uid"]},
        ).fetchone()
    if row is None:
        raise ValueError("Plan leg not found.")
    return dict(row._mapping)


# Stored station names carry a leading flag emoji ("🇫🇷 Paris Gare de Lyon"), added
# by enrich_parsed_trip. Strip it before feeding the name back as a geocoding query.
_FLAG_RE = re.compile(r"^[\U0001F1E6-\U0001F1FF]{2}\s*")


def _strip_flag(name):
    return _FLAG_RE.sub("", name or "")


def _iata_from_name(name):
    """Recover the IATA code from a stored air-leg station name '… (CDG)'."""
    m = re.search(r"\(([A-Za-z]{3})\)\s*$", name or "")
    return m.group(1) if m else None


# ── tool schemas ──────────────────────────────────────────────────────────────


def _build_tools() -> list:
    return [
        {
            "name": "search_stations",
            "description": (
                "Resolve a place name to real stations/stops/airports with their "
                "exact coordinates, using Trainlog's own station search (the same "
                "one the website uses). ALWAYS call this FIRST to get coordinates "
                "for add_trip / add_plan_trip — pass the SAME `type` you will use "
                "for the trip so the right kind of stop is searched (e.g. railway "
                "stations for 'train', airports for 'air', ferry terminals for "
                "'ferry'). Use the lat/lng (and IATA, for flights) of the result "
                "that matches what the user meant. Only fall back to estimating "
                "coordinates from your own knowledge when this returns no usable "
                "match for the place."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Place / station / airport name to search for.",
                    },
                    "type": {
                        "type": "string",
                        "description": (
                            "Trip type to scope the search (default 'train'). "
                            "Determines which kind of stop is matched."
                        ),
                        "default": "train",
                        "enum": TRIP_TYPE_VALUES,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 8, max 20).",
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "list_trips",
            "description": (
                "List trips, most recent first, as a compact summary per trip. "
                "By default lists the authenticated user's own trips (all "
                "visibilities, including price). Pass `username` to read a "
                "FRIEND's trips instead: this is read-only and only returns their "
                "public/friends-visibility trips, with private fields (price, "
                "notes, seat, booking refs) omitted. Use list_friends to find "
                "whose trips you can read."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "Read this friend's trips (read-only). Omit for your own.",
                    },
                    "trip_type": {
                        "type": "string",
                        "description": "Optional filter by trip type.",
                        "enum": TRIP_TYPE_VALUES,
                    },
                    "upcoming_only": {
                        "type": "boolean",
                        "description": "Only future / planned trips.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max trips to return (default 50, max 500).",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "get_trip",
            "description": (
                "Return details for one trip by id. Works for your own trips "
                "(full detail, including price/notes) and for a friend's trip "
                "when its visibility is public or friends (reduced detail, "
                "private fields omitted). Otherwise: not found."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"trip_id": {"type": "integer"}},
                "required": ["trip_id"],
            },
        },
        {
            "name": "list_friends",
            "description": (
                "List the users you are friends with (accepted friendships). "
                "Their trips can be read via list_trips/get_trip, read-only."
            ),
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "add_trip",
            "description": (
                "Create a new trip. One trip = ONE segment (a flight with a "
                "connection is two trips). Trainlog geocodes the stations, routes "
                "the path, splits distance across countries and resolves "
                "timezones for you, so you only supply the facts below.\n\n"
                "ALWAYS provide origin_lat/lng and destination_lat/lng. Get them "
                "by calling search_stations FIRST (with the same `type`) and using "
                "the matching result's coordinates; only estimate coordinates from "
                "your own knowledge when search_stations returns no usable match. "
                "For flights (type='air'), also provide origin_iata/destination_iata "
                "(search_stations returns them); the airport database is then "
                "authoritative.\n\n"
                "Dates are YYYY-MM-DD; times are HH:MM local. Omit times for a "
                "date-only trip. Put booking reference / ticket number / class in "
                "the dedicated fields, not in notes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "default": "train",
                        "enum": TRIP_TYPE_VALUES,
                    },
                    "origin": {"type": "string", "description": "Origin station/stop name as shown."},
                    "origin_city": {"type": "string", "description": "City name only, aids geocoding."},
                    "origin_iata": {"type": "string", "description": "Airport IATA code (flights only)."},
                    "origin_lat": {"type": "number", "description": "Origin latitude (required)."},
                    "origin_lng": {"type": "number", "description": "Origin longitude (required)."},
                    "destination": {"type": "string", "description": "Destination station/stop name as shown."},
                    "destination_city": {"type": "string", "description": "City name only, aids geocoding."},
                    "destination_iata": {"type": "string", "description": "Airport IATA code (flights only)."},
                    "destination_lat": {"type": "number", "description": "Destination latitude (required)."},
                    "destination_lng": {"type": "number", "description": "Destination longitude (required)."},
                    "date": {"type": "string", "description": "Departure date YYYY-MM-DD. Defaults to today."},
                    "arrival_date": {"type": "string", "description": "Arrival date YYYY-MM-DD (overnight trips)."},
                    "time_departure": {"type": "string", "description": "Local departure time HH:MM."},
                    "time_arrival": {"type": "string", "description": "Local arrival time HH:MM."},
                    "operator": {"type": "string", "description": "Operating company."},
                    "line_name": {"type": "string", "description": "Flight number / train number / line."},
                    "price": {"type": "number"},
                    "currency": {"type": "string", "description": "ISO 4217 code, e.g. EUR, USD."},
                    "aircraft_icao": {"type": "string", "description": "Aircraft type, e.g. B738, A320 (flights)."},
                    "seat": {"type": "string"},
                    "booking_reference": {"type": "string", "description": "PNR / booking reference."},
                    "ticket_number": {"type": "string"},
                    "cabin_class": {"type": "string", "description": "Economy / Business / First."},
                    "notes": {"type": "string", "description": "Free-form notes. Keep personally-identifying data out."},
                },
                "required": ["origin", "destination", "origin_lat", "origin_lng",
                             "destination_lat", "destination_lng"],
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
        {
            "name": "list_trip_types",
            "description": "Return the valid trip type values accepted by add_trip.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        },
        # ── plans (parallel-world itineraries) ──────────────────────────────
        {
            "name": "list_plans",
            "description": (
                "List the user's plans (draft itineraries, separate from logged "
                "trips). Returns uuid, name, trip count and aggregate stats. "
                "Archived plans are included; filter with active_only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "active_only": {"type": "boolean", "description": "Exclude archived plans."},
                },
                "required": [],
            },
        },
        {
            "name": "get_plan",
            "description": (
                "Return one plan with its legs (plan trips, in itinerary order) "
                "and its shared costs. Use the leg `uid` and cost `uid` with the "
                "other plan tools."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"plan_uuid": {"type": "string"}},
                "required": ["plan_uuid"],
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
                    "anchor_date": {"type": "string", "description": "ISO date YYYY-MM-DD. Day 1. Defaults to today."},
                },
                "required": ["name"],
            },
        },
        {
            "name": "update_plan",
            "description": "Update a plan's name / description / anchor_date / archived flag. Only supplied fields change.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "anchor_date": {"type": "string", "description": "ISO date YYYY-MM-DD."},
                    "archived": {"type": "boolean"},
                },
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
                "Add a leg to a plan. Same geocoding/routing as add_trip: call "
                "search_stations FIRST (with the same `type`) to get accurate "
                "origin/destination lat/lng (and IATA for flights), and only "
                "estimate coordinates yourself when it returns no match.\n\n"
                "Timing is one of:\n"
                "  • Relative (default): start_day / end_day (1 = anchor_date) with "
                "optional start_time / end_time (HH:MM). This is the native plan "
                "mode — use it unless the user gave real calendar dates.\n"
                "  • Precise: start_datetime / end_datetime (YYYY-MM-DD HH:MM).\n"
                "  • Date-only: date (YYYY-MM-DD).\n"
                "  • Omit all timing for an undated leg.\n\n"
                "Set booked=true if the ticket is already purchased/ordered."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "type": {"type": "string", "default": "train", "enum": TRIP_TYPE_VALUES},
                    "origin": {"type": "string"},
                    "origin_iata": {"type": "string", "description": "Airport IATA (flights)."},
                    "origin_lat": {"type": "number"},
                    "origin_lng": {"type": "number"},
                    "destination": {"type": "string"},
                    "destination_iata": {"type": "string", "description": "Airport IATA (flights)."},
                    "destination_lat": {"type": "number"},
                    "destination_lng": {"type": "number"},
                    "start_day": {"type": "integer", "description": "Relative day of departure (1 = anchor_date)."},
                    "end_day": {"type": "integer", "description": "Relative day of arrival (defaults to start_day)."},
                    "start_time": {"type": "string", "description": "Local departure time HH:MM (relative mode)."},
                    "end_time": {"type": "string", "description": "Local arrival time HH:MM (relative mode)."},
                    "start_datetime": {"type": "string", "description": "Precise departure YYYY-MM-DD HH:MM."},
                    "end_datetime": {"type": "string", "description": "Precise arrival YYYY-MM-DD HH:MM."},
                    "date": {"type": "string", "description": "Date-only YYYY-MM-DD."},
                    "operator": {"type": "string"},
                    "line_name": {"type": "string"},
                    "aircraft_icao": {"type": "string"},
                    "seat": {"type": "string"},
                    "price": {"type": "number"},
                    "currency": {"type": "string"},
                    "booked": {"type": "boolean", "description": "Ticket already purchased/ordered."},
                    "notes": {"type": "string"},
                },
                "required": ["plan_uuid", "origin", "destination", "origin_lat",
                             "origin_lng", "destination_lat", "destination_lng"],
            },
        },
        {
            "name": "delete_plan_trip",
            "description": "Remove one leg from a plan.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "plan_trip_uid": {"type": "integer"},
                },
                "required": ["plan_uuid", "plan_trip_uid"],
            },
        },
        {
            "name": "update_plan_trip",
            "description": (
                "Update any field of a plan leg. Only supplied fields change; "
                "everything else is kept.\n\n"
                "Route: supplying any of origin/destination (name, lat/lng or "
                "iata) or `type` re-geocodes and re-routes the leg — call "
                "search_stations FIRST for the endpoint you are changing; the "
                "unchanged endpoint is reused as stored.\n\n"
                "Timing: supplying any timing field re-times the leg, in the same "
                "modes as add_plan_trip (relative start_day/end_day/start_time/"
                "end_time, precise start_datetime/end_datetime, or date-only "
                "`date`). Give fields from one mode only; when the leg is already "
                "in that mode, missing fields keep their current values (e.g. "
                "sending only start_time keeps the day), and shifting start_day "
                "alone shifts end_day with it. Pass an empty string to drop a "
                "start_time/end_time. Set clear_timing=true to make the leg "
                "undated.\n\n"
                "Metadata: booked, price/currency, seat, operator, line_name, "
                "aircraft_icao, notes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "plan_trip_uid": {"type": "integer"},
                    "type": {"type": "string", "enum": TRIP_TYPE_VALUES},
                    "origin": {"type": "string"},
                    "origin_iata": {"type": "string", "description": "Airport IATA (flights)."},
                    "origin_lat": {"type": "number"},
                    "origin_lng": {"type": "number"},
                    "destination": {"type": "string"},
                    "destination_iata": {"type": "string", "description": "Airport IATA (flights)."},
                    "destination_lat": {"type": "number"},
                    "destination_lng": {"type": "number"},
                    "start_day": {"type": "integer", "description": "Relative day of departure (1 = anchor_date)."},
                    "end_day": {"type": "integer", "description": "Relative day of arrival."},
                    "start_time": {"type": "string", "description": "Local departure time HH:MM (relative mode). Empty string clears it."},
                    "end_time": {"type": "string", "description": "Local arrival time HH:MM (relative mode). Empty string clears it."},
                    "start_datetime": {"type": "string", "description": "Precise departure YYYY-MM-DD HH:MM."},
                    "end_datetime": {"type": "string", "description": "Precise arrival YYYY-MM-DD HH:MM."},
                    "date": {"type": "string", "description": "Date-only YYYY-MM-DD."},
                    "clear_timing": {"type": "boolean", "description": "Remove all timing (undated leg)."},
                    "operator": {"type": "string"},
                    "line_name": {"type": "string"},
                    "aircraft_icao": {"type": "string"},
                    "seat": {"type": "string"},
                    "price": {"type": "number"},
                    "currency": {"type": "string"},
                    "booked": {"type": "boolean", "description": "Ticket already purchased/ordered."},
                    "notes": {"type": "string"},
                },
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
                    "ordered_uids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Leg uids in the order you want them.",
                    },
                },
                "required": ["plan_uuid", "ordered_uids"],
            },
        },
        {
            "name": "import_trips_to_plan",
            "description": (
                "Move existing real (logged) trips into a plan by their trip ids. "
                "The originals are removed from the trip log (this is the reverse "
                "of validating a plan). Only your own trips are moved."
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
                    "currency": {"type": "string", "description": "ISO 4217, e.g. EUR. Defaults to plan owner's currency."},
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
                "properties": {
                    "plan_uuid": {"type": "string"},
                    "cost_uid": {"type": "integer"},
                },
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
        "distance_km": round(r["trip_length"] / 1000, 1) if r.get("trip_length") is not None else None,
        "is_project": r.get("is_project"),
        "visibility": r.get("visibility"),
    }
    if include_private:
        out["price"] = r.get("price")
        out["currency"] = r.get("currency")
    return out


def _plan_summary(p: dict) -> dict:
    return {
        "plan_uuid": p["uuid"],
        "name": p["name"],
        "description": p.get("description"),
        "anchor_date": _iso(p.get("anchor_date")),
        "archived": p.get("archived"),
        "trip_count": p.get("trip_count"),
        "total_distance_km": round(p["total_distance_km"], 1) if p.get("total_distance_km") is not None else None,
    }


def _plan_trip_summary(r: dict) -> dict:
    return {
        "uid": r["uid"],
        "type": r["trip_type"],
        "origin": r["origin_station"],
        "destination": r["destination_station"],
        "timing_mode": r.get("timing_mode"),
        "start_day": r.get("start_day"),
        "end_day": r.get("end_day"),
        "start": _iso(r.get("start_datetime")),
        "end": _iso(r.get("end_datetime")),
        "operator": r.get("operator"),
        "line_name": r.get("line_name"),
        "distance_km": round(r["trip_length"] / 1000, 1) if r.get("trip_length") is not None else None,
        "price": r.get("price"),
        "currency": r.get("currency"),
        "booked": r.get("booked"),
        "cost_uid": r.get("cost_id"),
        "sort_order": r.get("sort_order"),
    }


def _plan_cost_summary(r: dict) -> dict:
    return {
        "uid": r["uid"],
        "name": r["name"],
        "price": r.get("price"),
        "currency": r.get("currency"),
        "notes": r.get("notes"),
        "leg_count": r.get("leg_count"),
    }


# ── station search ────────────────────────────────────────────────────────────


def _search_stations(query: str, trip_type: str, limit: int) -> list[dict]:
    """Resolve a place name to candidate stations/stops with their coordinates.

    Same backend the website's station autocomplete uses (templates/new.html):
    airports come from Trainlog's airport database, everything else from the
    Photon geocoder filtered by the trip type's OSM tags. Returns a list of
    {name, lat, lng, country, iata?} dicts, best match first."""
    query = (query or "").strip()
    if not query:
        return []

    # Flights resolve against the authoritative airport database (IATA/ICAO/name).
    if trip_type == "air":
        with pg_session() as pg:
            rows = pg.execute(
                get_airports_query(), {"searchPattern": "%" + query + "%"}
            ).fetchall()
        out = []
        for r in rows[:limit]:
            a = dict(r._mapping)
            out.append({
                "name": a.get("name"),
                "iata": a.get("iata"),
                "lat": a.get("latitude"),
                "lng": a.get("longitude"),
                "country": a.get("iso_country"),
            })
        return out

    params = {"q": query, "lang": "en"}
    tags = STATION_SEARCH_OSM_TAGS.get(trip_type)
    if tags:
        params["osm_tag"] = tags  # requests serialises a list as repeated params
    resp = photonRequest("/api", params=params, timeout=3)
    if resp is None:
        raise ValueError("Station search is temporarily unavailable.")

    out = []
    for feature in resp.get("features", [])[:limit]:
        props = feature.get("properties", {})
        lon, lat = (feature.get("geometry", {}).get("coordinates") or [None, None])[:2]
        name = props.get("name")
        city = props.get("city")
        if city and city != name:
            name = f"{city} - {name}" if name else city
        out.append({
            "name": name,
            "lat": lat,
            "lng": lon,
            "country": props.get("countrycode"),
            "type": props.get("osm_value"),
        })
    return out


# ── tool implementations ──────────────────────────────────────────────────────


def _call_tool(name: str, args: dict, user) -> str:
    uid = user.uid

    if name == "search_stations":
        trip_type = args.get("type", "train")
        if trip_type not in TRIP_TYPE_VALUES:
            raise ValueError(f"Unknown trip type: {trip_type}")
        limit = max(1, min(int(args.get("limit", 8)), 20))
        results = _search_stations(args.get("query", ""), trip_type, limit)
        return json.dumps(results, indent=2)

    if name == "list_trips":
        limit = max(1, min(int(args.get("limit", 50)), 500))
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
            clauses.append(
                "(is_project OR COALESCE(utc_start_datetime, start_datetime) >= NOW())"
            )
        where = " AND ".join(clauses)
        with pg_session() as pg:
            rows = pg.execute(
                f"""
                SELECT trip_id, trip_type, origin_station, destination_station,
                       start_datetime, end_datetime, operator, line_name,
                       trip_length, price, currency, is_project, visibility
                FROM trips
                WHERE {where}
                ORDER BY (is_project AND utc_start_datetime IS NULL) DESC,
                         COALESCE(utc_start_datetime, start_datetime) DESC NULLS LAST,
                         trip_id DESC
                LIMIT :limit
                """,
                params,
            ).fetchall()
        return json.dumps(
            [_trip_summary(dict(r._mapping), include_private=own) for r in rows],
            indent=2,
        )

    if name == "get_trip":
        with pg_session() as pg:
            row = pg.execute(
                "SELECT * FROM trips WHERE trip_id = :tid",
                {"tid": args["trip_id"]},
            ).fetchone()
        if row is None:
            raise ValueError("Trip not found.")
        d = dict(row._mapping)
        owner = d["user_id"] == uid
        if not owner:
            # A friend may read only public/friends trips, and only if friends.
            if d.get("visibility") not in FRIEND_VISIBLE or not _are_friends(uid, d["user_id"]):
                raise ValueError("Trip not found.")
        out = _trip_summary(d, include_private=owner)
        out["countries"] = d.get("countries")
        if owner:
            out.update({
                "seat": d.get("seat"),
                "material_type": d.get("material_type"),
                "reg": d.get("reg"),
                "notes": d.get("notes"),
                "purchase_date": _iso(d.get("purchase_date")),
                "created": _iso(d.get("created")),
            })
        return json.dumps(out, indent=2)

    if name == "list_friends":
        rows = (
            Friendship.query.filter(
                Friendship.user_id == uid, Friendship.accepted.isnot(None)
            ).all()
        )
        friend_uids = [f.friend_id for f in rows]
        friends = (
            User.query.filter(User.uid.in_(friend_uids)).all() if friend_uids else []
        )
        return json.dumps(sorted(u.username for u in friends), indent=2)

    if name == "add_trip":
        trip_type = args.get("type", "train")
        if trip_type not in TRIP_TYPE_VALUES:
            raise ValueError(f"Unknown trip type: {trip_type}")
        # Shape into the dict create_trip_from_parsed expects (the same schema the
        # internal AI parser produced). It geocodes, routes and resolves times.
        parsed = {
            "type": trip_type,
            "origin": args.get("origin", ""),
            "origin_city": args.get("origin_city"),
            "origin_iata": args.get("origin_iata"),
            "origin_lat": args.get("origin_lat"),
            "origin_lng": args.get("origin_lng"),
            "destination": args.get("destination", ""),
            "destination_city": args.get("destination_city"),
            "destination_iata": args.get("destination_iata"),
            "destination_lat": args.get("destination_lat"),
            "destination_lng": args.get("destination_lng"),
            "date": args.get("date"),
            "arrival_date": args.get("arrival_date"),
            "time_departure": args.get("time_departure"),
            "time_arrival": args.get("time_arrival"),
            "operator": args.get("operator"),
            "line_name": args.get("line_name"),
            "price": args.get("price"),
            "currency": args.get("currency"),
            "aircraft_icao": args.get("aircraft_icao"),
            "seat": args.get("seat"),
            "booking_reference": args.get("booking_reference"),
            "ticket_number": args.get("ticket_number"),
            "cabin_class": args.get("cabin_class"),
            "notes": args.get("notes"),
        }
        trip = create_trip_from_parsed(user, parsed, source="mcp")
        if trip is None:
            raise ValueError(
                "Could not create the trip (geocoding/routing failed). "
                "Check the station names and coordinates."
            )
        return json.dumps({
            "ok": True,
            "id": trip.trip_id,
            "origin": trip.origin_station,
            "destination": trip.destination_station,
        })

    if name == "delete_trip":
        with pg_session() as pg:
            row = pg.execute(
                "SELECT user_id FROM trips WHERE trip_id = :tid",
                {"tid": args["trip_id"]},
            ).fetchone()
        if row is None or row["user_id"] != uid:
            raise ValueError("Trip not found.")
        _delete_trip(args["trip_id"], user.username)
        return json.dumps({"ok": True})

    if name == "list_trip_types":
        return json.dumps(TRIP_TYPE_VALUES)

    # ── plans ───────────────────────────────────────────────────────────────
    if name == "list_plans":
        with pg_session() as pg:
            rows = pg.execute(get_user_plans_query(), {"user_id": uid}).fetchall()
        plans = [_plan_summary(dict(r._mapping)) for r in rows]
        if args.get("active_only"):
            plans = [p for p in plans if not p["archived"]]
        return json.dumps(plans, indent=2)

    if name == "get_plan":
        plan = _owned_plan(args["plan_uuid"], uid)
        with pg_session() as pg:
            trips = pg.execute(get_plan_trips_query(), {"plan_id": plan["uid"]}).fetchall()
            costs = pg.execute(get_plan_costs_query(), {"plan_id": plan["uid"]}).fetchall()
        return json.dumps({
            "plan_uuid": plan["uuid"],
            "name": plan["name"],
            "description": plan.get("description"),
            "anchor_date": _iso(plan.get("anchor_date")),
            "archived": plan.get("archived"),
            "trips": [_plan_trip_summary(dict(r._mapping)) for r in trips],
            "costs": [_plan_cost_summary(dict(r._mapping)) for r in costs],
        }, indent=2)

    if name == "create_plan":
        now = datetime.now()
        plan_uuid = str(_uuid.uuid4())
        with pg_session() as pg:
            pg.execute(insert_plan_query(), {
                "uuid": plan_uuid,
                "user_id": uid,
                "name": (args.get("name") or "Plan"),
                "description": args.get("description"),
                "anchor_date": args.get("anchor_date") or now.date(),
                "created": now,
                "last_modified": now,
            })
        return json.dumps({"ok": True, "plan_uuid": plan_uuid})

    if name == "update_plan":
        plan = _owned_plan(args["plan_uuid"], uid)
        now = datetime.now()
        with pg_session() as pg:
            if any(k in args for k in ("name", "description", "anchor_date")):
                pg.execute(update_plan_query(), {
                    "uid": plan["uid"], "user_id": uid,
                    "name": args.get("name", plan["name"]),
                    "description": args.get("description", plan.get("description")),
                    "anchor_date": args.get("anchor_date") or plan["anchor_date"],
                    "last_modified": now,
                })
            if "archived" in args:
                pg.execute(archive_plan_query(), {
                    "uid": plan["uid"], "user_id": uid,
                    "archived": bool(args["archived"]), "last_modified": now,
                })
        return json.dumps({"ok": True})

    if name == "delete_plan":
        plan = _owned_plan(args["plan_uuid"], uid)
        _delete_plan(plan["uid"], uid)
        return json.dumps({"ok": True})

    if name == "add_plan_trip":
        plan = _owned_plan(args["plan_uuid"], uid)
        trip_type = args.get("type", "train")
        if trip_type not in TRIP_TYPE_VALUES:
            raise ValueError(f"Unknown trip type: {trip_type}")
        parsed = {
            "type": trip_type,
            "origin": args.get("origin", ""),
            "origin_iata": args.get("origin_iata"),
            "origin_lat": args.get("origin_lat"),
            "origin_lng": args.get("origin_lng"),
            "destination": args.get("destination", ""),
            "destination_iata": args.get("destination_iata"),
            "destination_lat": args.get("destination_lat"),
            "destination_lng": args.get("destination_lng"),
            "operator": args.get("operator"),
            "line_name": args.get("line_name"),
            "aircraft_icao": args.get("aircraft_icao"),
            "seat": args.get("seat"),
            "price": args.get("price"),
            "currency": args.get("currency"),
            "notes": args.get("notes"),
        }
        if args.get("start_day") is not None:
            timing_input = {
                "precision": "relative",
                "planStartDay": args.get("start_day"),
                "planEndDay": args.get("end_day") or args.get("start_day"),
                "planStartTime": args.get("start_time") or "",
                "planEndTime": args.get("end_time") or "",
            }
        elif args.get("start_datetime"):
            timing_input = {
                "precision": "preciseDates",
                "newTripStart": str(args["start_datetime"]).replace(" ", "T"),
                "newTripEnd": str(args.get("end_datetime") or args["start_datetime"]).replace(" ", "T"),
            }
        elif args.get("date"):
            timing_input = {"precision": "onlyDate", "onlyDate": args["date"], "onlyDateDuration": ""}
        else:
            timing_input = {"precision": "unknown", "unknownType": "future"}

        pt = create_plan_trip_from_parsed(
            plan, parsed, timing_input, booked=bool(args.get("booked", False))
        )
        if pt is None:
            raise ValueError(
                "Could not add the leg (geocoding/routing failed). "
                "Check the station names and coordinates."
            )
        return json.dumps({
            "ok": True, "plan_trip_uid": pt.uid,
            "origin": pt.origin_station, "destination": pt.destination_station,
        })

    if name == "delete_plan_trip":
        plan = _owned_plan(args["plan_uuid"], uid)
        _delete_plan_trip(args["plan_trip_uid"], uid)
        return json.dumps({"ok": True})

    if name == "update_plan_trip":
        plan = _owned_plan(args["plan_uuid"], uid)
        pt = _owned_plan_trip(plan, args["plan_trip_uid"])

        route_keys = ("type", "origin", "origin_iata", "origin_lat", "origin_lng",
                      "destination", "destination_iata", "destination_lat",
                      "destination_lng")
        relative_keys = ("start_day", "end_day", "start_time", "end_time")
        precise_keys = ("start_datetime", "end_datetime")
        meta_keys = ("operator", "line_name", "aircraft_icao", "seat", "price",
                     "currency", "booked", "notes")
        if not any(k in args for k in
                   route_keys + relative_keys + precise_keys + meta_keys
                   + ("date", "clear_timing")):
            raise ValueError("Nothing to update.")

        trip_type = args.get("type") or pt["trip_type"]
        if trip_type not in TRIP_TYPE_VALUES:
            raise ValueError(f"Unknown trip type: {trip_type}")

        stored_path = [
            {"lat": c[0], "lng": c[1]} for c in geom_geojson_to_coords(pt["geojson"])
        ]

        # ── route: re-geocode/re-route when any endpoint (or the type) changes,
        # reusing the stored endpoint for the side that wasn't supplied.
        reroute = any(k in args for k in route_keys)
        if reroute:
            parsed = enrich_parsed_trip({
                "type": trip_type,
                "origin": args.get("origin") or _strip_flag(pt["origin_station"]),
                "origin_iata": args.get("origin_iata")
                    or _iata_from_name(pt["origin_station"]),
                "origin_lat": args.get("origin_lat")
                    or (stored_path[0]["lat"] if stored_path else None),
                "origin_lng": args.get("origin_lng")
                    or (stored_path[0]["lng"] if stored_path else None),
                "destination": args.get("destination")
                    or _strip_flag(pt["destination_station"]),
                "destination_iata": args.get("destination_iata")
                    or _iata_from_name(pt["destination_station"]),
                "destination_lat": args.get("destination_lat")
                    or (stored_path[-1]["lat"] if stored_path else None),
                "destination_lng": args.get("destination_lng")
                    or (stored_path[-1]["lng"] if stored_path else None),
            })
            if not parsed:
                raise ValueError(
                    "Could not update the leg (geocoding/routing failed). "
                    "Check the station names and coordinates."
                )
            path = parsed["_path"]
            trip_length = parsed["_distance"]
            if trip_type in ("air", "helicopter"):
                countries = json.dumps({
                    getCountryFromCoordinates(path[0]["lat"], path[0]["lng"])["countryCode"]: trip_length / 2,
                    getCountryFromCoordinates(path[-1]["lat"], path[-1]["lng"])["countryCode"]: trip_length / 2,
                })
            else:
                countries = getCountriesFromPath(path, trip_type, None, None)
            origin_station = parsed["_resolved_origin"]
            destination_station = parsed["_resolved_destination"]
        else:
            path = stored_path or [{"lat": 0.0, "lng": 0.0}, {"lat": 0.0, "lng": 0.0}]
            trip_length = pt["trip_length"]
            countries = pt["countries"]
            origin_station = pt["origin_station"]
            destination_station = pt["destination_station"]

        # ── timing: rebuild when any timing field is supplied, filling gaps from
        # the current values when the leg is already in that mode.
        modes_given = [m for m, keys in (
            ("relative", relative_keys), ("preciseDates", precise_keys), ("onlyDate", ("date",)),
        ) if any(k in args for k in keys)]
        if len(modes_given) > 1 or (modes_given and args.get("clear_timing")):
            raise ValueError("Give timing fields from one mode only.")

        if args.get("clear_timing"):
            timing_input = {"precision": "unknown", "unknownType": "future"}
        elif modes_given == ["relative"]:
            was_relative = pt["timing_mode"] == "relative"
            old_start = pt["start_day"] if was_relative else None
            old_end = pt["end_day"] if was_relative else None
            start_day = args.get("start_day") or old_start or 1
            if args.get("end_day"):
                end_day = args["end_day"]
            elif "start_day" in args and old_start and old_end:
                end_day = old_end + (start_day - old_start)  # keep the leg's span
            else:
                end_day = old_end or start_day

            def _fmt_time(t):
                return t.strftime("%H:%M") if t else ""

            timing_input = {
                "precision": "relative",
                "planStartDay": start_day,
                "planEndDay": end_day,
                "planStartTime": (args["start_time"] if "start_time" in args
                                  else _fmt_time(was_relative and pt["start_time"])) or "",
                "planEndTime": (args["end_time"] if "end_time" in args
                                else _fmt_time(was_relative and pt["end_time"])) or "",
            }
        elif modes_given == ["preciseDates"]:
            was_precise = pt["timing_mode"] == "preciseDates"

            def _fmt_dt(d):
                return d.strftime("%Y-%m-%dT%H:%M") if d else None

            start = args.get("start_datetime") or (
                _fmt_dt(pt["start_datetime"]) if was_precise else None
            )
            if not start:
                raise ValueError("start_datetime is required to switch to precise timing.")
            end = args.get("end_datetime") or (
                _fmt_dt(pt["end_datetime"]) if was_precise else None
            ) or start
            timing_input = {
                "precision": "preciseDates",
                "newTripStart": str(start).replace(" ", "T"),
                "newTripEnd": str(end).replace(" ", "T"),
            }
        elif modes_given == ["onlyDate"]:
            timing_input = {"precision": "onlyDate", "onlyDate": args["date"],
                            "onlyDateDuration": ""}
        else:
            timing_input = None  # timing untouched

        if timing_input is not None:
            timing = process_plan_dates(timing_input, path)
        else:
            timing = {k: pt[k] for k in (
                "timing_mode", "start_day", "end_day", "start_time", "end_time",
                "start_datetime", "end_datetime", "utc_start_datetime",
                "utc_end_datetime", "manual_trip_duration",
            )}

        # Concrete UTC instants give a precise scheduled duration (as in
        # create_plan_trip_from_parsed); a reroute invalidates the old one.
        estimated = None if reroute else pt["estimated_trip_duration"]
        if timing.get("utc_start_datetime") and timing.get("utc_end_datetime"):
            diff = int((timing["utc_end_datetime"] - timing["utc_start_datetime"]).total_seconds())
            if diff >= 0:
                estimated = diff

        def merged(key):
            return args[key] if key in args else pt[key]

        plan_trip = PlanTrip(
            plan_id=plan["uid"],
            user_id=plan["user_id"],
            origin_station=origin_station,
            destination_station=destination_station,
            trip_type=trip_type,
            operator=merged("operator"),
            line_name=merged("line_name"),
            material_type=(args.get("aircraft_icao") if "aircraft_icao" in args
                           else pt["material_type"]),
            material_type_advanced=pt["material_type_advanced"],
            reg=pt["reg"],
            seat=merged("seat"),
            notes=merged("notes"),
            trip_length=trip_length,
            estimated_trip_duration=estimated,
            countries=countries,
            price=merged("price"),
            currency=merged("currency"),
            waypoints=None if reroute else pt["waypoints"],
            visibility=pt["visibility"],
            path=path,
            timing=timing,
            purchase_date=pt["purchase_date"],
            booked=bool(merged("booked")),
            power_type=pt["power_type"],
            co2_override=pt["co2_override"],
            sort_order=pt["sort_order"],
            created=pt["created"],
            last_modified=datetime.now(),
        )
        update_plan_trip_full(args["plan_trip_uid"], plan_trip)
        return json.dumps({
            "ok": True,
            "plan_trip_uid": args["plan_trip_uid"],
            "origin": plan_trip.origin_station,
            "destination": plan_trip.destination_station,
        })

    if name == "reorder_plan_trips":
        plan = _owned_plan(args["plan_uuid"], uid)
        order = args.get("ordered_uids") or []
        with pg_session() as pg:
            for i, leg_uid in enumerate(order):
                pg.execute(reorder_plan_trip_query(), {
                    "uid": int(leg_uid), "plan_id": plan["uid"],
                    "user_id": uid, "sort_order": i,
                })
        return json.dumps({"ok": True})

    if name == "import_trips_to_plan":
        plan = _owned_plan(args["plan_uuid"], uid)
        ids = [int(x) for x in (args.get("trip_ids") or [])]
        moved = import_trips_to_plan(plan, ids) if ids else []
        return json.dumps({"ok": True, "moved_trip_ids": moved})

    if name == "add_plan_cost":
        plan = _owned_plan(args["plan_uuid"], uid)
        now = datetime.now()
        with pg_session() as pg:
            cost_uid = pg.execute(insert_plan_cost_query(), {
                "plan_id": plan["uid"],
                "name": args["name"],
                "price": float(args["price"]),
                "currency": args.get("currency") or user.user_currency,
                "notes": args.get("notes"),
                "created": now, "last_modified": now,
            }).fetchone()[0]
        return json.dumps({"ok": True, "cost_uid": cost_uid})

    if name == "update_plan_cost":
        plan = _owned_plan(args["plan_uuid"], uid)
        with pg_session() as pg:
            row = pg.execute(
                "SELECT name, price, currency, notes FROM plan_costs "
                "WHERE uid = :uid AND plan_id = :pid",
                {"uid": args["cost_uid"], "pid": plan["uid"]},
            ).fetchone()
            if row is None:
                raise ValueError("Cost not found.")
            cur = dict(row._mapping)
            pg.execute(update_plan_cost_query(), {
                "uid": args["cost_uid"], "plan_id": plan["uid"],
                "name": args.get("name", cur["name"]),
                "price": float(args["price"]) if "price" in args else cur["price"],
                "currency": args.get("currency", cur["currency"]),
                "notes": args.get("notes", cur["notes"]),
                "last_modified": datetime.now(),
            })
        return json.dumps({"ok": True})

    if name == "delete_plan_cost":
        plan = _owned_plan(args["plan_uuid"], uid)
        with pg_session() as pg:
            pg.execute(delete_plan_cost_query(), {"uid": args["cost_uid"], "plan_id": plan["uid"]})
        return json.dumps({"ok": True})

    if name == "assign_trip_cost":
        plan = _owned_plan(args["plan_uuid"], uid)
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
                "uid": args["plan_trip_uid"], "plan_id": plan["uid"],
                "cost_id": cost_uid, "last_modified": datetime.now(),
            })
            if res.rowcount == 0:
                raise ValueError("Plan leg not found.")
        return json.dumps({"ok": True})

    raise ValueError(f"Unknown tool: {name}")


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────


def _handle_rpc(msg: dict, user) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Trainlog", "version": "1.0"},
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": _build_tools()})

    if method == "tools/call":
        params = msg.get("params", {})
        try:
            text = _call_tool(params.get("name", ""), params.get("arguments", {}), user)
            return ok({"content": [{"type": "text", "text": text}]})
        except ValueError as e:
            return ok({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
        except Exception as e:
            logger.exception("MCP tool error")
            return err(-32603, str(e))

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
        responses = [r for r in (_handle_rpc(m, user) for m in msg) if r is not None]
        return Response(json.dumps(responses), status=200, content_type="application/json")

    response = _handle_rpc(msg, user)
    if response is None:
        return Response(status=202)
    return Response(json.dumps(response), status=200, content_type="application/json")
