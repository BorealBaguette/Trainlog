"""Live tracking of in-progress flights.

A flight logged before it happens has no real route: it is saved as a 2-point LINESTRING
and drawn as a geodesic. While it is airborne FR24 serves the flown-so-far track, so we
fetch it and draw the flown portion for real, leaving only the remainder estimated. Once
it has landed the track is promoted into `paths`, exactly as a manual FR24 import would.

Each trip moves through one life cycle, kept in live_flight_tracks:

    pending / unresolved -> live -> landed (promoted, row deleted)
                                 -> ended  (lost for good; the trip keeps its logged route)

Cost control is the whole design problem, since a map view must not map 1:1 onto a paid
API call. The refresh floor is therefore per trip and globally shared rather than per
viewer: however many people are looking at a flight, it is refreshed at most once every
LIVE_POLL_INTERVAL, and that refresh is charged to the traveller.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, jsonify, session
from geopy.distance import geodesic

from py.utils import getDistanceFromPath, interpolate_track_if_gaps
from src.paths import coords_to_ewkt, geom_geojson_to_coords
from src.pg import pg_session
from src.runway_stops import airport_elevation, delay_fields, has_runway_stop, track_stops, with_stops
from src.users import User
from src.utils import check_and_increment_fr24_usage, current_user_is_friend_with, load_config, owner as site_owner

logger = logging.getLogger(__name__)

live_tracks_blueprint = Blueprint("live_tracks", __name__)

# Refresh floors. Enforced in Postgres, not in memory: SimpleCache is per-gunicorn-worker,
# so an in-process guard would let every worker poll independently. The whole track every
# few minutes; between two, the latest position alone (a cheaper call), appended to it.
# Both tunable in config.yaml (FR24.live_track_seconds, FR24.live_position_seconds): what a
# tracked flight costs per hour is 3600 / each.
_FR24_CONFIG = load_config().get("FR24") or {}
LIVE_POLL_INTERVAL = timedelta(seconds=int(_FR24_CONFIG.get("live_track_seconds", 300)))
# By the last altitude: close to the ground (climbing out, on the approach) the position
# changes the most, at cruise the least. Below LOW_ALT_M every live_position_low_seconds,
# up to HIGH_ALT_M every live_position_seconds, above every live_position_high_seconds.
# (FR24's Explorer plan allows 10 requests a minute in all: 5 s is 12 for one flight.)
LIVE_POSITION_INTERVAL = timedelta(seconds=int(_FR24_CONFIG.get("live_position_seconds", 10)))
LIVE_POSITION_LOW = timedelta(seconds=int(_FR24_CONFIG.get("live_position_low_seconds", 5)))
LIVE_POSITION_HIGH = timedelta(seconds=int(_FR24_CONFIG.get("live_position_high_seconds", 60)))
LOW_ALT_M, HIGH_ALT_M = 1000, 2000
# The interval for the trail's last altitude, in SQL (live_flight_tracks columns)
_POSITION_EVERY_SQL = """
    CASE WHEN COALESCE(CAST(altitude->>-1 AS double precision), 0) < :low_alt THEN :every_low
         WHEN CAST(altitude->>-1 AS double precision) > :high_alt THEN :every_high
         ELSE :every END"""
_POSITION_EVERY_PARAMS = {"every": LIVE_POSITION_INTERVAL, "every_low": LIVE_POSITION_LOW,
                          "every_high": LIVE_POSITION_HIGH, "low_alt": LOW_ALT_M, "high_alt": HIGH_ALT_M}

# Polling window, relative to the trip's own times so that a long-haul leg is never cut
# off mid-flight. The grace period covers delays; the hard ceiling catches an arrival
# mistyped days into the future. Landing detection, not this window, is what normally
# ends tracking.
ARRIVAL_GRACE = timedelta(hours=6)
MAX_FLIGHT_WINDOW = timedelta(hours=20)

FR24_BASE = "https://fr24api.flightradar24.com"
FR24_TIMEOUT = 25

# FR24 reports 0 ft for an aircraft on the ground.
GROUND_ALT_M = 50

# Coverage often ends before the runway: radar contact is lost low on the approach, and
# the trail simply stops. An aircraft that went silent this low and this close to its
# destination has landed there.
SIGNAL_LOST = timedelta(minutes=15)
APPROACH_KM = 30
APPROACH_ALT_M = 2000

# Silent this long near its destination, FR24's summary is asked whether the flight is over
# (cheaper than the track, and it knows when it landed) rather than waiting out SIGNAL_LOST.
SUMMARY_AFTER = timedelta(minutes=3)

# Silent this long past its scheduled arrival, far from the destination: FR24 will not
# hear from it again. Long enough to ride out an oceanic coverage gap.
SIGNAL_GONE = timedelta(minutes=60)
# How long past its (delay-adjusted) arrival a silent flight is still waited for.
GIVE_UP_AFTER = timedelta(hours=3)

# After touchdown the aircraft is followed to the gate, whose time the connection after
# the flight counts from: until FR24 has not heard it for this long (transponder off)...
PARKED_SILENCE = timedelta(minutes=10)
# ...or it has been on the ground this long, for a transponder left on at the stand. Long,
# because transponders go off as the passengers leave: one still heard is most likely
# waiting for its stand, and that wait is part of its arrival.
PARKED_AFTER = timedelta(minutes=30)


def _fr24_headers():
    config = load_config()
    return {
        "Accept": "application/json",
        "Accept-Version": "v1",
        "Authorization": f"Bearer {config['FR24']['token_auth']}",
    }


def _fr24_epoch(ts):
    """Normalise an FR24 timestamp (ISO 8601 or epoch number) to epoch seconds."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return int(ts)
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _as_utc(value):
    """trips.utc_* columns are naive UTC timestamps."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _flight_number(trip):
    """The flight number as FR24 writes it: "AF 0012" is "AF12"."""
    number = re.sub(r"\s+", "", trip.get("line_name") or "").upper()
    return re.sub(r"^([A-Z0-9]{2}[A-Z]?)0+(?=\d)", r"\1", number)


def _flight_key(trip):
    """What a resolved FR24 instance belongs to. Any change means it is another flight.

    The registration only counts when it is what the flight is resolved by: otherwise
    filling it in from FR24 would itself look like a change of flight.
    """
    identity = _flight_number(trip) or (trip.get("reg") or "").strip().upper()
    return f"{identity}|{trip['scheduled_dep'].isoformat()}"


# --------------------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------------------

# The delay-adjusted in-progress window, widened by the grace period. Already-promoted
# trips stay in (as `landed`) so a viewer is told the flight is over and handed its final
# track. COALESCE on route_source because older rows may be NULL rather than 'router'.
_ELIGIBLE_SQL = """
    SELECT t.trip_id,
           t.user_id,
           t.line_name,
           t.reg,
           t.utc_start_datetime AS scheduled_dep,
           t.utc_end_datetime AS scheduled_arr,
           t.departure_delay,
           t.arrival_delay,
           t.waypoints,
           COALESCE(t.route_source, 'router') = 'fr24' AS landed,
           ST_Y(ST_StartPoint(p.geom)) AS orig_lat,
           ST_X(ST_StartPoint(p.geom)) AS orig_lng,
           ST_Y(ST_EndPoint(p.geom)) AS dest_lat,
           ST_X(ST_EndPoint(p.geom)) AS dest_lng,
           t.utc_end_datetime + COALESCE(t.arrival_delay, 0) * interval '1 second' AS arr
    FROM trips t
    LEFT JOIN paths p ON p.trip_id = t.trip_id
    WHERE t.trip_id = ANY(:ids)
      AND t.trip_type = 'air'
      AND t.utc_start_datetime IS NOT NULL
      AND t.utc_end_datetime IS NOT NULL
      AND NOW() >= t.utc_start_datetime + COALESCE(t.departure_delay, 0) * interval '1 second'
      AND NOW() <= LEAST(
            t.utc_end_datetime   + COALESCE(t.arrival_delay, 0)   * interval '1 second'
                                 + :grace,
            t.utc_start_datetime + COALESCE(t.departure_delay, 0) * interval '1 second'
                                 + :max_window)
"""


def eligible_trips(pg, trip_ids):
    """Return (trips, owners) for the subset of trip_ids that are in-progress flights whose
    owner has opted into live tracking.

    The opt-in check cannot be a SQL join: trips live in Postgres while User lives in the
    separate SQLite auth DB.
    """
    if not trip_ids:
        return [], {}
    rows = pg.execute(
        _ELIGIBLE_SQL,
        {
            "ids": [int(t) for t in trip_ids],
            "grace": ARRIVAL_GRACE,
            "max_window": MAX_FLIGHT_WINDOW,
        },
    ).fetchall()
    rows = [dict(r._mapping) for r in rows]
    if not rows:
        return [], {}

    owners = {
        u.uid: u
        for u in User.query.filter(
            User.uid.in_({r["user_id"] for r in rows}), User.premium, User.live_tracking
        ).all()
    }
    trips = []
    for row in rows:
        owner = owners.get(row["user_id"])
        if owner:
            row["username"] = owner.username
            row["fill_details"] = owner.live_tracking_details
            trips.append(row)
    return trips, owners


# --------------------------------------------------------------------------------------
# FR24 calls
# --------------------------------------------------------------------------------------


def _resolve_fr24_id(trip):
    """Find the currently-airborne FR24 flight instance for this trip.

    FR24 ids are per-flight-*instance* and only exist once the flight is live, so this is
    resolved here, once per trip, then cached on the live row. When several instances are
    airborne at once (yesterday's delayed long-haul overlapping today's) we take the one
    whose ETA is closest to the trip's arrival.
    """
    flight_number = _flight_number(trip)
    registration = (trip.get("reg") or "").strip().upper()
    if not flight_number and not registration:
        return None

    params = (
        {"flights": flight_number} if flight_number else {"registrations": registration}
    )
    if not check_and_increment_fr24_usage(username=trip["username"]):
        return None
    try:
        resp = requests.get(
            f"{FR24_BASE}/api/live/flight-positions/full",
            headers=_fr24_headers(),
            params=params,
            timeout=FR24_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("FR24 resolve failed for trip %s: %s", trip["trip_id"], exc)
        return None

    matches = resp.json().get("data", [])
    if not matches:
        return None

    arr = _as_utc(trip["arr"]).timestamp()

    def eta_distance(match):
        eta = _fr24_epoch(match.get("eta"))
        return float("inf") if eta is None else abs(eta - arr)

    return min(matches, key=eta_distance)


def _fetch_summary(fr24_id, username):
    """FR24's summary of a flight instance (flight_ended, datetime_landed...), or None."""
    if not check_and_increment_fr24_usage(username=username):
        return None
    try:
        resp = requests.get(
            f"{FR24_BASE}/api/flight-summary/light",
            headers=_fr24_headers(),
            params={"flight_ids": fr24_id},
            timeout=FR24_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("FR24 summary failed for %s: %s", fr24_id, exc)
        return None
    return next((f for f in resp.json().get("data", []) if f.get("fr24_id") == fr24_id), None)


def _fetch_position(trip, fr24_id):
    """The aircraft's latest position from FR24's live feed: [lat, lng, altitude m, epoch]
    or None. The feed is searched by flight number (registration), as when resolving the
    flight, and the instance is told apart by its id."""
    flight_number = _flight_number(trip)
    registration = (trip.get("reg") or "").strip().upper()
    if not flight_number and not registration:
        return None
    params = {"flights": flight_number} if flight_number else {"registrations": registration}
    if not check_and_increment_fr24_usage(username=trip["username"]):
        return None
    try:
        resp = requests.get(
            f"{FR24_BASE}/api/live/flight-positions/light",
            headers=_fr24_headers(), params=params, timeout=FR24_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("FR24 position failed for trip %s: %s", trip["trip_id"], exc)
        return None
    match = next((m for m in resp.json().get("data", []) if m.get("fr24_id") == fr24_id), None)
    when = _fr24_epoch(match.get("timestamp")) if match else None
    if not match or when is None or match.get("lat") is None or match.get("lon") is None:
        return None
    return [match["lat"], match["lon"], round((match.get("alt") or 0) * 0.3048, 1), when]


def _fetch_track(fr24_id, username):
    """Fetch the whole trail so far. Returns (coords, altitude, timestamps) or None."""
    if not check_and_increment_fr24_usage(username=username):
        return None
    try:
        resp = requests.get(
            f"{FR24_BASE}/api/flight-tracks",
            headers=_fr24_headers(),
            params={"flight_id": fr24_id},
            timeout=FR24_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("FR24 track fetch failed for %s: %s", fr24_id, exc)
        return None

    if not data or "tracks" not in data[0]:
        return None

    # Same shape as the manual import: feet -> metres, timestamp -> epoch seconds, then
    # interpolated so the three arrays stay aligned with the geom vertices.
    enriched = [
        [t["lat"], t["lon"], round((t.get("alt") or 0) * 0.3048, 1), _fr24_epoch(t.get("timestamp"))]
        for t in data[0]["tracks"]
        if "lat" in t and "lon" in t
    ]
    if len(enriched) < 2:
        return None
    enriched = interpolate_track_if_gaps(enriched, 50)

    coords = [[p[0], p[1]] for p in enriched]
    altitude = [p[2] for p in enriched]
    timestamps = [p[3] for p in enriched]
    return coords, altitude, timestamps


# --------------------------------------------------------------------------------------
# Flight state
# --------------------------------------------------------------------------------------


def _touched_down(altitude):
    """Seen back on the ground after having been up. Two ground points, not one, so a
    single bad 0 ft sample mid-flight cannot end it."""
    return (
        len(altitude) > 10
        and max(altitude) > GROUND_ALT_M
        and all(a <= GROUND_ALT_M for a in altitude[-2:])
    )


def _near_destination(trip, fix, alt):
    if trip.get("dest_lat") is None or trip.get("dest_lng") is None:
        return False
    return (
        alt <= APPROACH_ALT_M
        and geodesic(fix, (trip["dest_lat"], trip["dest_lng"])).km <= APPROACH_KM
    )


def _flight_state(trip, coords, altitude, timestamps):
    """'live', 'landed' or 'ended', from the observed trail.

    Never from the clock alone: landing overwrites the trip's stored path, so calling a
    delayed flight landed at its scheduled arrival would freeze its route mid-air.
    """
    now = datetime.now(timezone.utc)
    last_seen = timestamps[-1]
    silence = now - datetime.fromtimestamp(last_seen, timezone.utc) if last_seen else timedelta(0)
    if _touched_down(altitude):
        landed_at = next(t for a, t in zip(reversed(altitude), reversed(timestamps))
                         if a > GROUND_ALT_M)
        on_ground = timedelta(seconds=last_seen - landed_at) if last_seen and landed_at else timedelta(0)
        return "landed" if silence >= PARKED_SILENCE or on_ground >= PARKED_AFTER else "live"
    if silence >= SIGNAL_LOST and _near_destination(trip, coords[-1], altitude[-1]):
        return "landed"
    # Given up for good (no longer polled): only well past even a late arrival, the
    # delay not always being logged, and a long-haul's coverage gaps lasting an hour.
    if silence >= SIGNAL_GONE and now > _as_utc(trip["arr"]) + GIVE_UP_AFTER:
        return "ended"
    return "live"


# --------------------------------------------------------------------------------------
# Refresh
# --------------------------------------------------------------------------------------


def _claim_due_trips(pg, trips):
    """Claim the trips that are due a refresh, and return them.

    The claim IS the mutex: last_polled is set in a single conditional UPDATE, so when N
    requests race on the same cold trip exactly one matches and the others serve the stored
    row. It is stamped before the fetch, so a failing flight backs off for the full
    interval instead of being retried on every view.

    Being unresolved is the normal state just after scheduled departure (FR24's live
    endpoint only knows aircraft that are out of the gate), so unresolved trips are retried
    on the same interval as every other, or the climb would go unseen.
    """
    if not trips:
        return []
    by_id = {t["trip_id"]: t for t in trips}
    # New rows, plus a fresh start for any row resolved for another flight: a corrected
    # flight number or departure makes the old aircraft worthless.
    pg.execute(
        """
        INSERT INTO live_flight_tracks (trip_id, last_polled, status, flight_key)
        SELECT id, to_timestamp(0), 'pending', key
        FROM unnest(CAST(:ids AS integer[]), CAST(:keys AS text[])) AS k(id, key)
        ON CONFLICT (trip_id) DO UPDATE
        SET fr24_id = NULL, geom = NULL, altitude = NULL, timestamps = NULL, status = 'pending',
            last_polled = to_timestamp(0), flight_key = EXCLUDED.flight_key
        WHERE live_flight_tracks.flight_key IS DISTINCT FROM EXCLUDED.flight_key
        """,
        {"ids": list(by_id), "keys": [_flight_key(t) for t in by_id.values()]},
    )
    rows = pg.execute(
        """
        UPDATE live_flight_tracks
        SET last_polled = NOW(), poll_count = poll_count + 1
        WHERE trip_id = ANY(CAST(:ids AS integer[]))
          AND status <> 'ended'
          AND NOW() - last_polled >= :interval
        RETURNING trip_id, fr24_id
        """,
        {"ids": list(by_id), "interval": LIVE_POLL_INTERVAL},
    ).fetchall()
    return [{**by_id[row["trip_id"]], "fr24_id": row["fr24_id"]} for row in rows]


def _claim_due_positions(pg, trips):
    """Claim the live flights due their latest position (not their whole track: one just
    claimed for that is left out, the track brings it), as _claim_due_trips does."""
    if not trips:
        return []
    by_id = {t["trip_id"]: t for t in trips}
    rows = pg.execute(
        """
        UPDATE live_flight_tracks
        SET position_polled = NOW()
        WHERE trip_id = ANY(CAST(:ids AS integer[]))
          AND status = 'live' AND fr24_id IS NOT NULL AND GeometryType(geom) = 'LINESTRING'
          AND NOW() - position_polled >= """ + _POSITION_EVERY_SQL + """
          AND NOW() - last_polled >= """ + _POSITION_EVERY_SQL + """
        RETURNING trip_id, fr24_id
        """,
        {"ids": list(by_id), "ground": GROUND_ALT_M, **_POSITION_EVERY_PARAMS},
    ).fetchall()
    return [(by_id[row["trip_id"]], row["fr24_id"]) for row in rows]


def _store_position(pg, trip_id, fix):
    """Append the latest position to the stored trail, if it is newer than its end."""
    lat, lng, alt, when = fix
    pg.execute(
        """
        UPDATE live_flight_tracks
        SET geom = ST_AddPoint(geom, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)),
            altitude = altitude || to_jsonb(CAST(:alt AS double precision)),
            timestamps = timestamps || to_jsonb(CAST(:ts AS bigint)),
            -- Touched down (up at the last position, down now): the whole track at once, for
            -- the real approach, touchdown and landing time rather than 30 s jumps to the stand
            last_polled = CASE WHEN :alt <= :ground AND CAST(altitude->>-1 AS double precision) > :ground
                               THEN to_timestamp(0) ELSE last_polled END
        WHERE trip_id = :trip_id AND status = 'live'
          AND GeometryType(geom) = 'LINESTRING'
          AND jsonb_typeof(altitude) = 'array' AND jsonb_typeof(timestamps) = 'array'
          AND COALESCE(CAST(timestamps->>-1 AS double precision), 0) < :ts
        """,
        {"trip_id": trip_id, "lat": lat, "lng": lng, "alt": alt, "ts": int(when), "ground": GROUND_ALT_M},
    )


def _fetch_update(trip):
    """Do the network work for one claimed trip. Runs with no database session open, so
    no connection is pinned across two 25s HTTP calls."""
    update = {"fr24_id": trip.get("fr24_id")}
    if not update["fr24_id"]:
        match = _resolve_fr24_id(trip)
        if not match:
            return {"status": "unresolved"}
        update = {
            "fr24_id": match["fr24_id"],
            "aircraft_type": match.get("type"),
            "aircraft_reg": match.get("reg"),
        }

    track = _fetch_track(update["fr24_id"], trip["username"])
    if track is None:
        return {**update, "status": "pending"}

    coords, altitude, timestamps = track
    status = _flight_state(trip, coords, altitude, timestamps)
    landed_at = None
    if (status == "live" and timestamps[-1]
            and datetime.now(timezone.utc).timestamp() - timestamps[-1] >= SUMMARY_AFTER.total_seconds()
            and _near_destination(trip, coords[-1], altitude[-1])):
        summary = _fetch_summary(update["fr24_id"], trip["username"])
        if summary and summary.get("flight_ended"):
            status = "landed"
            landed_at = _fr24_epoch(summary.get("datetime_landed"))
    # A trail never seen in the air (FR24 only heard it taxiing, at either end) shows
    # nothing of the flight: the trip keeps the route it was logged with.
    if status == "landed" and max(altitude) <= GROUND_ALT_M:
        status = "ended"
    estimated = False
    if status == "landed" and altitude[-1] > GROUND_ALT_M:
        # Lost on the approach: finish the line at the airport it was landing at, when FR24
        # says it landed, else reached at the speed it was last flying (marked estimated).
        dest = [trip["dest_lat"], trip["dest_lng"]]
        coords.append(dest)
        altitude.append(0)
        timestamps.append(landed_at or _estimated_arrival(coords[-3:-1], timestamps[-2:], dest))
        estimated = not landed_at and timestamps[-1] is not None
    return {
        "estimated_landing": estimated,
        **update,
        "status": status,
        "coords": coords,
        "altitude": altitude,
        "timestamps": timestamps,
    }


def _estimated_arrival(last_fixes, last_times, dest):
    """When an aircraft lost on its approach reached `dest`: the rest of the way at the
    speed between its last two fixes (approach speed when those are missing)."""
    (a, b), (ta, tb) = last_fixes, last_times
    if not tb:
        return None
    moved = geodesic(a, b).meters
    speed = moved / (tb - ta) if ta and tb > ta and moved > 0 else 70
    return tb + geodesic(b, dest).meters / max(speed, 40)


def _promote_to_paths(pg, trip, update):
    """Write the finished track into `paths` and drop the live row, leaving the trip
    indistinguishable from a manual FR24 import."""
    coords = update["coords"]
    pg.execute(
        "DELETE FROM live_flight_tracks WHERE trip_id = :trip_id",
        {"trip_id": trip["trip_id"]},
    )
    pg.execute(
        """
        INSERT INTO paths (trip_id, geom, altitude, timestamps)
        VALUES (:trip_id, ST_GeomFromEWKT(:geom), CAST(:altitude AS jsonb),
                CAST(:timestamps AS jsonb))
        ON CONFLICT (trip_id) DO UPDATE
        SET geom = EXCLUDED.geom,
            altitude = EXCLUDED.altitude,
            timestamps = EXCLUDED.timestamps
        """,
        {
            "trip_id": trip["trip_id"],
            "geom": coords_to_ewkt(coords),
            "altitude": json.dumps(update["altitude"]),
            "timestamps": json.dumps(update["timestamps"]),
        },
    )
    # Take-off and landing as stops; the delays only for owners who let FR24 fill in
    # their flight.
    stops = track_stops(coords, update["altitude"], update["timestamps"])
    if update.get("estimated_landing"):
        for stop in stops:
            if stop["stop"]["kind"] == "landing":
                stop["stop"]["estimated"] = True
    fields = {"waypoints": with_stops(
        trip["waypoints"], stops,
        trip["scheduled_dep"], trip["scheduled_arr"], trip["departure_delay"], trip["arrival_delay"],
    )}
    if trip["fill_details"]:
        fields.update(delay_fields(
            fields["waypoints"], trip["scheduled_dep"], trip["scheduled_arr"],
            trip["departure_delay"], trip["arrival_delay"],
        ))
    sets = "".join(f", {column} = :{column}" for column in fields)
    # last_modified because map clients cache paths in IndexedDB keyed on it: without the
    # bump they would keep drawing the straight line.
    pg.execute(
        f"""
        UPDATE trips SET route_source = 'fr24', trip_length = :length,
                         last_modified = :now{sets}
        WHERE trip_id = :trip_id
        """,
        {
            "trip_id": trip["trip_id"],
            "length": getDistanceFromPath(coords)[-1],
            "now": datetime.now(),
            **fields,
        },
    )
    logger.info("Promoted live track for trip %s into paths", trip["trip_id"])


def _keep_newer_positions(pg, trip_id, update):
    """FR24's whole track lags behind its live positions: the positions stored since past
    its end are kept on after it (in `update`), so a track never cuts the trail back."""
    if not update.get("timestamps") or not update["timestamps"][-1]:
        return
    row = pg.execute(
        "SELECT ST_AsGeoJSON(geom) AS g, altitude, timestamps FROM live_flight_tracks WHERE trip_id = :t",
        {"t": trip_id},
    ).fetchone()
    if not row or not row["g"] or not isinstance(row["timestamps"], list) or not isinstance(row["altitude"], list):
        return
    coords = geom_geojson_to_coords(row["g"])
    if len(coords) != len(row["timestamps"]) or len(coords) != len(row["altitude"]):
        return
    end = update["timestamps"][-1]
    for c, a, t in zip(coords, row["altitude"], row["timestamps"]):
        if t and t > end:
            update["coords"].append(c)
            update["altitude"].append(a)
            update["timestamps"].append(t)


def _trail(pg, trip_id):
    """(coords, altitude, timestamps) of a live row, or None."""
    row = pg.execute(
        "SELECT ST_AsGeoJSON(geom) AS g, altitude, timestamps FROM live_flight_tracks WHERE trip_id = :t",
        {"t": trip_id},
    ).fetchone()
    if not row or not row["g"] or not isinstance(row["altitude"], list) or not isinstance(row["timestamps"], list):
        return None
    coords = geom_geojson_to_coords(row["g"])
    return (coords, row["altitude"], row["timestamps"]) if len(coords) == len(row["timestamps"]) else None


def _arrived(coords, altitude, timestamps):
    """Landed and stood still: up before, on the ground now, and barely moving over its
    last minute (or not heard from for two). Taxiing in, it is not yet: it is still drawn
    moving, and its positions still asked for."""
    if not altitude or max(a or 0 for a in altitude) <= GROUND_ALT_M:
        return False
    if not all(a is not None and a <= GROUND_ALT_M for a in altitude[-2:]):
        return False
    last = timestamps[-1] if timestamps else None
    if not last:
        return False
    # FR24 has had nothing newer for two minutes, though asked all along (it stops being
    # asked only once this says so): its transponder is off at the stand
    if datetime.now(timezone.utc).timestamp() - last > 120:
        return True
    # Stood still for a minute: its fixes of the last 60 s all within 15 m of the last one.
    # Not two fixes alike: FR24 repeats a ground position while the aircraft creeps along.
    recent = [i for i, t in enumerate(timestamps) if t and last - t <= 60]
    if not recent or last - timestamps[recent[0]] < 50:
        return False
    return all(geodesic(coords[i], coords[-1]).meters < 15 for i in recent)


def _store_update(pg, trip, update):
    """Persist one refresh result."""
    status = update["status"]
    if status == "landed":
        _store_aircraft(pg, trip, update)
        _promote_to_paths(pg, trip, update)
        return
    if status == "unresolved":
        pg.execute(
            "UPDATE live_flight_tracks SET status = 'unresolved' WHERE trip_id = :trip_id",
            {"trip_id": trip["trip_id"]},
        )
        return

    _store_aircraft(pg, trip, update)
    if status == "pending":
        return
    _keep_newer_positions(pg, trip["trip_id"], update)
    pg.execute(
        """
        UPDATE live_flight_tracks
        SET geom = ST_GeomFromEWKT(:geom),
            altitude = CAST(:altitude AS jsonb),
            timestamps = CAST(:timestamps AS jsonb),
            status = :status
        WHERE trip_id = :trip_id
        """,
        {
            "trip_id": trip["trip_id"],
            "status": status,
            "geom": coords_to_ewkt(update["coords"]),
            "altitude": json.dumps(update["altitude"]),
            "timestamps": json.dumps(update["timestamps"]),
        },
    )
    if status == "live":
        _store_runway_stops(pg, trip, update)


def _store_runway_stops(pg, trip, update):
    """The take-off and the taxi before it, then the landing, each written as soon as the
    track shows it rather than when the flight is finalised: the timeline has the take-off
    while it flies, the landing while it taxies in. Finalising writes the final ones."""
    stops = track_stops(update["coords"], update["altitude"], update["timestamps"])
    kinds = {stop["stop"]["kind"] for stop in stops}
    new = [k for k in ("takeoff", "landing") if k in kinds and not has_runway_stop(trip["waypoints"], k)]
    if not new:
        return
    fields = {"waypoints": with_stops(
        trip["waypoints"], stops, trip["scheduled_dep"], trip["scheduled_arr"],
        trip["departure_delay"], trip["arrival_delay"],
    )}
    if trip["fill_details"]:
        fields.update(delay_fields(
            fields["waypoints"], trip["scheduled_dep"], trip["scheduled_arr"],
            trip["departure_delay"], trip["arrival_delay"],
        ))
    sets = ", ".join(f"{column} = :{column}" for column in fields)
    pg.execute(
        f"UPDATE trips SET {sets}, last_modified = :now WHERE trip_id = :trip_id",
        {"trip_id": trip["trip_id"], "now": datetime.now(), **fields},
    )


def _store_aircraft(pg, trip, update):
    """Cache the resolved instance and, for owners who asked for it, record the aircraft
    FR24 sees flying it: it is known as soon as the flight is, and cannot change after.
    Times come with the track (_store_runway_stops, _promote_to_paths)."""
    pg.execute(
        "UPDATE live_flight_tracks SET fr24_id = :fr24_id WHERE trip_id = :trip_id",
        {"trip_id": trip["trip_id"], "fr24_id": update["fr24_id"]},
    )
    if not trip["fill_details"]:
        return
    details = {
        column: update[key]
        for column, key in (("material_type", "aircraft_type"), ("reg", "aircraft_reg"))
        if update.get(key)
    }
    if not details:
        return
    sets = ", ".join(f"{column} = :{column}" for column in details)
    pg.execute(
        f"UPDATE trips SET {sets}, last_modified = :now WHERE trip_id = :trip_id",
        {"trip_id": trip["trip_id"], "now": datetime.now(), **details},
    )


def _recent_speed(coords, timestamps):
    """Ground speed (m/s) over about the last minute of fixes, or None: what the trip page
    carries the aircraft on at between two of them, rather than the timetable's pace."""
    if not isinstance(timestamps, list) or len(timestamps) != len(coords) or len(coords) < 2:
        return None
    last = len(coords) - 1
    if not timestamps[last]:
        return None
    first = last
    while first > 0 and timestamps[first - 1] and timestamps[last] - timestamps[first - 1] <= 75:
        first -= 1
    span = timestamps[last] - timestamps[first] if first < last else 0
    if span < 20:
        # Too few fixes for a minute's average (just after a gap): its last two, if they
        # are a few seconds apart at least
        prev = timestamps[last - 1]
        if not prev or timestamps[last] - prev < 3:
            return None
        speed = geodesic(coords[last - 1], coords[last]).meters / (timestamps[last] - prev)
        return round(speed, 1) if speed <= 350 else None
    metres = sum(geodesic(a, b).meters for a, b in zip(coords[first:last], coords[first + 1:last + 1]))
    speed = metres / span
    return round(speed, 1) if 0 <= speed <= 350 else None


def _recent_vspeed(altitude, timestamps):
    """Climb (+) or descent (-) rate, m/s, over about the last 40 s of fixes, or None: what
    the trip page carries the 3D aircraft's height on at between two of them."""
    if not isinstance(altitude, list) or not isinstance(timestamps, list) or len(altitude) != len(timestamps):
        return None
    last = len(altitude) - 1
    if last < 1 or not timestamps[last] or altitude[last] is None:
        return None
    first = last
    while first > 0 and timestamps[first - 1] and altitude[first - 1] is not None \
            and timestamps[last] - timestamps[first - 1] <= 45:
        first -= 1
    span = timestamps[last] - timestamps[first] if first < last else 0
    if span < 8:
        return None
    rate = (altitude[last] - altitude[first]) / span
    return round(rate, 2) if abs(rate) <= 60 else None


def _load_payloads(pg, trip_ids):
    """Read back what is stored for these trips, in client shape: the live trail of the
    ones in the air, and the final track of the ones that have landed."""
    rows = pg.execute(
        """
        SELECT trip_id, 'live' AS status, ST_AsGeoJSON(geom) AS geojson, altitude,
               timestamps,
               -- When the last fix was taken, which the client shows: the stored row can
               -- be a whole refresh interval old, and its timestamps may be stripped.
               COALESCE(CAST(timestamps->>-1 AS double precision),
                        EXTRACT(EPOCH FROM last_polled)) AS updated,
               -- When a request will next bring something new: the position or the track
               EXTRACT(EPOCH FROM LEAST(last_polled + :track_every,
                                        GREATEST(position_polled, last_polled) + """ + _POSITION_EVERY_SQL + """)) AS next_update
        FROM live_flight_tracks
        WHERE trip_id = ANY(CAST(:ids AS integer[]))
          AND status = 'live' AND geom IS NOT NULL
        UNION ALL
        SELECT p.trip_id, 'landed', ST_AsGeoJSON(p.geom), p.altitude, p.timestamps, NULL, NULL
        FROM paths p
        JOIN trips t ON t.trip_id = p.trip_id
        WHERE p.trip_id = ANY(CAST(:ids AS integer[])) AND t.route_source = 'fr24'
        """,
        {"ids": [int(t) for t in trip_ids], "track_every": LIVE_POLL_INTERVAL, **_POSITION_EVERY_PARAMS},
    ).fetchall()
    payloads = {}
    for row in rows:
        coords = geom_geojson_to_coords(row["geojson"])
        if not coords or len(coords) < 2:
            continue
        altitude = row["altitude"] if isinstance(row["altitude"], list) else None
        payloads[row["trip_id"]] = {
            "trip_id": row["trip_id"],
            "status": row["status"],
            # Up, and back on the ground: landed and taxiing in, nothing left to fly. Told
            # apart here, before the altitude is withheld from viewers (flight_3d), so the
            # maps stop drawing a remainder to the airport's point from the gate.
            "arrived": row["status"] == "live" and bool(altitude) and _arrived(coords, altitude, row["timestamps"]),
            # On the ground now (taxiing): not carried on ahead of its fixes, it turns
            "on_ground": bool(row["status"] == "live" and altitude and altitude[-1] is not None
                              and altitude[-1] <= GROUND_ALT_M),
            "path": coords,
            "altitude": row["altitude"],
            "timestamps": row["timestamps"],
            "updated": int(row["updated"]) if row["updated"] else None,
            "next_update": int(row["next_update"]) if row["next_update"] else None,
            # Measured here, as the timestamps may be withheld from the viewer (flight_3d)
            "speed": _recent_speed(coords, row["timestamps"]) if row["status"] == "live" else None,
            "vspeed": _recent_vspeed(row["altitude"], row["timestamps"]) if row["status"] == "live" else None,
        }
    return payloads


def get_live_tracks(trip_ids, positions=False):
    """Return {trip_id: {status, path, altitude, timestamps, updated}} for whichever of
    trip_ids are opted-in flights in their tracking window. `status` is 'live' while in
    the air and 'landed' once the final track is in `paths`; a trip missing from the
    result is not (or no longer) tracked. Shared by the endpoint and the /live_map
    enrichment, so both get identical geometry and identical cost behaviour.

    Split into three phases so that no database session is ever held across an HTTP call:
    claim (short transaction), fetch (no session), store (short transaction).
    """
    with pg_session() as pg:
        trips, owners = eligible_trips(pg, trip_ids)
        if not trips:
            return {}
        claimed = _claim_due_trips(pg, [t for t in trips if not t["landed"]])

    updates = []
    for trip in claimed:
        try:
            updates.append((trip, _fetch_update(trip)))
        except Exception:
            # A live overlay is decoration: never let it break the map it sits on.
            logger.exception("Live track refresh failed for trip %s", trip["trip_id"])

    # One session per store: a failed statement aborts its whole transaction in Postgres,
    # so a shared one would let a single bad trip take down every other trip's write.
    for trip, update in updates:
        try:
            with pg_session() as pg:
                _store_update(pg, trip, update)
        except Exception:
            logger.exception("Storing live track failed for trip %s", trip["trip_id"])

    # Between two tracks, the latest position of the flights in the air: for a trip's own
    # page only (`positions`), where someone follows that flight. The live map, with every
    # flight in the air on it, makes do with the tracks (and any position those pages got).
    due = []
    if positions:
        with pg_session() as pg:
            due = _claim_due_positions(pg, [t for t in trips if not t["landed"]])
    for trip, fr24_id in due:
        try:
            with pg_session() as pg:
                trail = _trail(pg, trip["trip_id"])
            if trail and _arrived(*trail):
                continue   # at its stand: the whole tracks finish it
            fix = _fetch_position(trip, fr24_id)
            if fix:
                with pg_session() as pg:
                    _store_position(pg, trip["trip_id"], fix)
        except Exception:
            logger.exception("Live position refresh failed for trip %s", trip["trip_id"])

    with pg_session() as pg:
        payloads = _load_payloads(pg, [t["trip_id"] for t in trips])
        # Its two airports' elevations, for heights above its runways in the 3D view
        for trip in trips:
            if trip["trip_id"] in payloads:
                payloads[trip["trip_id"]]["elevations"] = [
                    airport_elevation(trip.get("orig_lat"), trip.get("orig_lng"), pg=pg),
                    airport_elevation(trip.get("dest_lat"), trip.get("dest_lng"), pg=pg),
                ]

    # The 3D altitude profile is separately opt-in per owner (see the show_3d gate on the
    # trip page): live tracking must not become a side door that exposes it.
    owner_of = {t["trip_id"]: owners[t["user_id"]] for t in trips}
    for trip_id, payload in payloads.items():
        if not owner_of[trip_id].flight_3d:
            payload["altitude"] = None
            payload["timestamps"] = None
    return payloads


@live_tracks_blueprint.route("/live_tracks/<trip_ids>")
def live_tracks(trip_ids):
    """Live overlay for the given trips.

    Deliberately separate from getTripsPaths: that response is cached client-side in
    IndexedDB keyed on lastLocal, so a track served through it would freeze at whatever
    point a viewer first loaded and never update.
    """
    try:
        ids = [int(t) for t in trip_ids.split(",") if t.strip()]
    except ValueError:
        return jsonify({"error": "invalid trip ids"}), 400
    if not ids or len(ids) > 200:
        return jsonify({"error": "expected between 1 and 200 trip ids"}), 400
    return jsonify(get_live_tracks(_viewable_ids(ids), positions=True))


def _viewable_ids(ids):
    """The trips of `ids` this viewer may see, by the trip page's rules
    (processPublicTrips): the owner (and the site owner) sees everything; others only
    the trips of users who share theirs, never a private one, a friends one to friends.
    The endpoint takes any id, so it must not show the track of a trip whose page is shut."""
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT trip_id, user_id, visibility FROM trips WHERE trip_id = ANY(:ids)",
            {"ids": ids},
        ).fetchall()
    if not rows:
        return []
    users = {u.uid: u for u in User.query.filter(User.uid.in_({r["user_id"] for r in rows})).all()}
    friends, out = {}, []
    for row in rows:
        user = users.get(row["user_id"])
        if not user:
            continue
        if not (session.get(user.username) or session.get(site_owner)):
            if not user.is_public_trips() or row["visibility"] == "private":
                continue
            if row["visibility"] == "friends":
                if user.username not in friends:
                    friends[user.username] = current_user_is_friend_with(user.username)
                if not friends[user.username]:
                    continue
        out.append(row["trip_id"])
    return out
