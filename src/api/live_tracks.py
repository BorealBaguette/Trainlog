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
from flask import Blueprint, jsonify
from geopy.distance import geodesic

from py.utils import getDistanceFromPath, interpolate_track_if_gaps
from src.paths import coords_to_ewkt, geom_geojson_to_coords
from src.pg import pg_session
from src.runway_stops import delay_fields, has_runway_stop, track_stops, with_stops
from src.users import User
from src.utils import check_and_increment_fr24_usage, load_config

logger = logging.getLogger(__name__)

live_tracks_blueprint = Blueprint("live_tracks", __name__)

# Refresh floor. Enforced in Postgres, not in memory: SimpleCache is per-gunicorn-worker,
# so an in-process guard would let every worker poll independently.
LIVE_POLL_INTERVAL = timedelta(minutes=5)

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

# After touchdown the aircraft is followed to the gate, whose time the connection after
# the flight counts from: until FR24 has not heard it for this long (transponder off)...
PARKED_SILENCE = timedelta(minutes=10)
# ...or it has been on the ground this long, for a transponder left on at the stand. Long,
# because transponders go off as the passengers leave: one still heard is most likely
# waiting for its stand, and that wait is part of its arrival.
PARKED_AFTER = timedelta(minutes=90)


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
    if silence >= SIGNAL_GONE and now > _as_utc(trip["arr"]):
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
        _store_takeoff(pg, trip, update)


def _store_takeoff(pg, trip, update):
    """The take-off and the taxi before it, written once, as soon as the track shows them
    rather than at the landing: the timeline and the connection before the flight have
    them while it flies. The landing replaces it with the final one."""
    if has_runway_stop(trip["waypoints"], "takeoff"):
        return
    stops = [
        stop for stop in track_stops(update["coords"], update["altitude"], update["timestamps"])
        if stop["stop"]["kind"] == "takeoff"
    ]
    if not stops:
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
    Times come with the track (_store_takeoff, _promote_to_paths)."""
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
                        EXTRACT(EPOCH FROM last_polled)) AS updated
        FROM live_flight_tracks
        WHERE trip_id = ANY(CAST(:ids AS integer[]))
          AND status = 'live' AND geom IS NOT NULL
        UNION ALL
        SELECT p.trip_id, 'landed', ST_AsGeoJSON(p.geom), p.altitude, p.timestamps, NULL
        FROM paths p
        JOIN trips t ON t.trip_id = p.trip_id
        WHERE p.trip_id = ANY(CAST(:ids AS integer[])) AND t.route_source = 'fr24'
        """,
        {"ids": [int(t) for t in trip_ids]},
    ).fetchall()
    payloads = {}
    for row in rows:
        coords = geom_geojson_to_coords(row["geojson"])
        if not coords or len(coords) < 2:
            continue
        payloads[row["trip_id"]] = {
            "trip_id": row["trip_id"],
            "status": row["status"],
            "path": coords,
            "altitude": row["altitude"],
            "timestamps": row["timestamps"],
            "updated": int(row["updated"]) if row["updated"] else None,
        }
    return payloads


def get_live_tracks(trip_ids):
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

    with pg_session() as pg:
        payloads = _load_payloads(pg, [t["trip_id"] for t in trips])

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
    return jsonify(get_live_tracks(ids))
