"""Live tracking of in-progress flights.

A flight logged before it happens has no real route: it is saved as a 2-point LINESTRING
and drawn as a geodesic. That looks obviously fake next to a completed leg whose real,
wind-blown track came from the FR24 import — which can only run once the flight has landed.

While the flight is actually airborne, FR24 will serve the flown-so-far track (verified: the
tracks endpoint returns a partial trail whose last point is seconds old). So we fetch it and
draw the flown portion for real, leaving only the remainder estimated.

Cost control is the whole design problem here, since a map view must not map 1:1 onto a paid
API call. It is handled by making the refresh floor *per trip and globally shared* rather than
per viewer: however many people are looking at a flight, it is refreshed at most once every
LIVE_POLL_INTERVAL, and that refresh is charged to the traveller. A viewer therefore cannot
burn credits by reloading. See _refresh_if_stale for the three guards that enforce this.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, jsonify

from py.utils import getDistanceFromPath, interpolate_track_if_gaps
from src.paths import coords_to_ewkt, geom_geojson_to_coords
from src.pg import pg_session
from src.users import User
from src.utils import check_and_increment_fr24_usage, load_config

logger = logging.getLogger(__name__)

live_tracks_blueprint = Blueprint("live_tracks", __name__)

# Refresh floor. Enforced in Postgres, not in memory: SimpleCache is per-gunicorn-worker,
# so an in-process guard would let every worker poll independently.
LIVE_POLL_INTERVAL = timedelta(minutes=5)

# Polling window, expressed relative to the trip's own times rather than as a poll count so
# that a long-haul leg is never cut off mid-flight. The grace period absorbs unlogged delays
# so a flight is still tracked to touchdown; the hard ceiling exists only to bound a trip
# whose arrival was mistyped days into the future.
ARRIVAL_GRACE = timedelta(hours=2)
MAX_FLIGHT_WINDOW = timedelta(hours=20)

FR24_BASE = "https://fr24api.flightradar24.com"
FR24_TIMEOUT = 25

# An altitude at or below this (metres) on the last track point means the aircraft is on the
# ground. Combined with the scheduled-arrival check this is what triggers promotion.
GROUND_ALT_M = 50


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


# --------------------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------------------

# Mirrors the delay-adjusted in-progress window used by get_current_trip.sql, narrowed to
# flights that do not already have a real route. COALESCE on route_source because the column
# was added later (0037) and older rows may be NULL rather than 'router'.
_ELIGIBLE_SQL = """
    SELECT trip_id,
           user_id,
           line_name,
           reg,
           destination_station,
           last_modified,
           utc_start_datetime + COALESCE(departure_delay, 0) * interval '1 second' AS dep,
           utc_end_datetime   + COALESCE(arrival_delay, 0)   * interval '1 second' AS arr
    FROM trips
    WHERE trip_id = ANY(:ids)
      AND trip_type = 'air'
      AND COALESCE(route_source, 'router') <> 'fr24'
      AND utc_start_datetime IS NOT NULL
      AND utc_end_datetime IS NOT NULL
      AND NOW() >= utc_start_datetime + COALESCE(departure_delay, 0) * interval '1 second'
      AND NOW() <= LEAST(
            utc_end_datetime   + COALESCE(arrival_delay, 0)   * interval '1 second'
                               + :grace,
            utc_start_datetime + COALESCE(departure_delay, 0) * interval '1 second'
                               + :max_window)
"""


def eligible_trips(pg, trip_ids):
    """Return the subset of trip_ids that are in-progress flights whose owner has opted
    into live tracking.

    The premium/opt-in check cannot be a SQL join: trips live in Postgres while User lives
    in the separate SQLite auth DB. So it is done in two steps, the same way
    get_current_trips_data already filters on appear_on_global.
    """
    if not trip_ids:
        return []
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
        return []

    user_ids = {r["user_id"] for r in rows}
    allowed = {
        u.uid
        for u in User.query.filter(
            User.uid.in_(user_ids), User.premium, User.live_tracking
        ).all()
    }
    return [r for r in rows if r["user_id"] in allowed]


# --------------------------------------------------------------------------------------
# FR24 calls
# --------------------------------------------------------------------------------------


def _resolve_fr24_id(trip, username):
    """Find the currently-airborne FR24 flight instance for this trip.

    FR24 ids are per-flight-*instance* and only exist once the flight is live, so this
    cannot be stored at save time and has to be resolved here — once per trip, then cached
    on the live row.

    A flight number can in principle have two instances airborne at once (yesterday's
    delayed long-haul overlapping today's), so when several come back we take the one whose
    ETA is closest to the trip's scheduled arrival.
    """
    flight_number = (trip.get("line_name") or "").strip().upper()
    registration = (trip.get("reg") or "").strip().upper()
    if not flight_number and not registration:
        return None  # nothing to resolve against -> caller marks 'unresolved'

    params = (
        {"flights": flight_number} if flight_number else {"registrations": registration}
    )
    if not check_and_increment_fr24_usage(username=username):
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
    if len(matches) == 1:
        return matches[0]

    arr = _as_utc(trip.get("arr"))
    def eta_distance(match):
        eta = _fr24_epoch(match.get("eta"))
        if eta is None or arr is None:
            return float("inf")
        return abs(eta - arr.timestamp())

    return min(matches, key=eta_distance)


def _fetch_track(fr24_id, username):
    """Fetch the flown-so-far track. Returns (coords, altitude, timestamps) or None.

    Each poll returns the whole trail so far, so the stored track is overwritten rather
    than appended to.
    """
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

    # Same shape as the existing import: feet -> metres, timestamp -> epoch seconds, then
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
# Refresh + promotion
# --------------------------------------------------------------------------------------


def _promote_to_paths(pg, trip, coords, altitude, timestamps):
    """Write the finished track into `paths` and drop the live row.

    After this the trip is indistinguishable from a normal FR24 import, so the user never
    has to run the import by hand for a flight they logged in advance.
    """
    ewkt = coords_to_ewkt(coords)
    if not ewkt:
        return
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
            "geom": ewkt,
            "altitude": json.dumps(altitude),
            "timestamps": json.dumps(timestamps),
        },
    )
    # getDistanceFromPath returns cumulative metres per vertex; the total is the last one.
    distances = getDistanceFromPath(coords)
    pg.execute(
        """
        UPDATE trips SET route_source = 'fr24', trip_length = :length
        WHERE trip_id = :trip_id
        """,
        {"trip_id": trip["trip_id"], "length": distances[-1] if distances else 0},
    )
    pg.execute(
        "DELETE FROM live_flight_tracks WHERE trip_id = :trip_id",
        {"trip_id": trip["trip_id"]},
    )
    logger.info("Promoted live track for trip %s into paths", trip["trip_id"])


def _as_utc(value):
    """trips.utc_* columns are naive UTC timestamps, so give them a tzinfo before any
    comparison against an aware 'now'."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _has_landed(trip, altitude, timestamps):
    """Landed if the aircraft is on the ground at the end of the trail, or if we are past
    the scheduled arrival (the grace period in the eligibility window means we keep
    polling a little beyond it, which is what gives us a final complete track)."""
    now = datetime.now(timezone.utc)
    arrival = _as_utc(trip.get("arr"))
    if arrival and now > arrival:
        return True
    # Guard against the first points of the trail, which are also at ground level.
    if altitude and len(altitude) > 10 and altitude[-1] <= GROUND_ALT_M:
        return max(altitude) > GROUND_ALT_M
    return False


def _claim_due_trips(pg, trips):
    """Claim the trips that are due a refresh, and return them.

    The claim IS the mutex, and it is what stops a crowd of viewers from each buying an
    FR24 call. Setting last_polled inside a single conditional UPDATE is atomic, so when
    N requests race on the same cold trip exactly one UPDATE matches the WHERE and the
    losers get nothing back and serve the stored row instead.

    last_polled is stamped up front rather than after a successful fetch, so a flight that
    is failing to fetch backs off for the full interval instead of being retried on every
    view.
    """
    if not trips:
        return []
    by_id = {t["trip_id"]: t for t in trips}
    pg.execute(
        """
        INSERT INTO live_flight_tracks (trip_id, last_polled, status)
        SELECT unnest(CAST(:ids AS integer[])), to_timestamp(0), 'pending'
        ON CONFLICT (trip_id) DO NOTHING
        """,
        {"ids": list(by_id)},
    )
    # A corrected flight number means whatever we resolved before is worthless: clear it
    # so the row is resolved again rather than tracking the wrong aircraft.
    for trip in trips:
        pg.execute(
            """
            UPDATE live_flight_tracks
            SET fr24_id = NULL, status = 'pending'
            WHERE trip_id = :trip_id
              AND trip_seen_modified IS NOT NULL
              AND trip_seen_modified <> :modified
            """,
            {"trip_id": trip["trip_id"], "modified": str(trip.get("last_modified") or "")},
        )
    rows = pg.execute(
        """
        UPDATE live_flight_tracks
        SET last_polled = NOW(), poll_count = poll_count + 1
        WHERE trip_id = ANY(CAST(:ids AS integer[]))
          AND NOW() - last_polled >= :interval
          -- Never retry a flight FR24 has no record of: it would cost a call every
          -- interval for the whole trip, and can only start working if the user edits
          -- the trip, which is handled by the reset above.
          AND status <> 'unresolved'
        RETURNING trip_id, fr24_id
        """,
        {"ids": list(by_id), "interval": LIVE_POLL_INTERVAL},
    ).fetchall()
    claimed = []
    for row in rows:
        trip = dict(by_id[row["trip_id"]])
        trip["fr24_id"] = row["fr24_id"]
        claimed.append(trip)
    return claimed


def _fetch_update(trip, username):
    """Do the network work for one claimed trip. Runs with no database session open.

    That matters: these are two HTTP calls with a 25s timeout each, and holding a
    connection (or a lock) across them would pin it for the whole round trip. It also
    lets check_and_increment_fr24_usage open its own session, which it needs to.
    """
    fr24_id = trip.get("fr24_id")
    if not fr24_id:
        match = _resolve_fr24_id(trip, username)
        if not match:
            return {"status": "unresolved"}
        fr24_id = match["fr24_id"]

    track = _fetch_track(fr24_id, username)
    if track is None:
        return {"status": "pending", "fr24_id": fr24_id}

    coords, altitude, timestamps = track
    return {
        "status": "landed" if _has_landed(trip, altitude, timestamps) else "live",
        "fr24_id": fr24_id,
        "coords": coords,
        "altitude": altitude,
        "timestamps": timestamps,
    }


def _store_update(pg, trip, update):
    """Persist one refresh result."""
    trip_id = trip["trip_id"]
    modified = str(trip.get("last_modified") or "")

    if update["status"] == "unresolved":
        pg.execute(
            """
            UPDATE live_flight_tracks
            SET status = 'unresolved', trip_seen_modified = :modified
            WHERE trip_id = :trip_id
            """,
            {"trip_id": trip_id, "modified": modified},
        )
        return

    if update["status"] == "pending":
        pg.execute(
            """
            UPDATE live_flight_tracks
            SET fr24_id = :fr24_id, trip_seen_modified = :modified
            WHERE trip_id = :trip_id
            """,
            {"trip_id": trip_id, "fr24_id": update["fr24_id"], "modified": modified},
        )
        return

    if update["status"] == "landed":
        _promote_to_paths(pg, trip, update["coords"], update["altitude"], update["timestamps"])
        return

    pg.execute(
        """
        UPDATE live_flight_tracks
        SET fr24_id = :fr24_id,
            geom = ST_GeomFromEWKT(:geom),
            altitude = CAST(:altitude AS jsonb),
            timestamps = CAST(:timestamps AS jsonb),
            status = 'live',
            trip_seen_modified = :modified
        WHERE trip_id = :trip_id
        """,
        {
            "trip_id": trip_id,
            "fr24_id": update["fr24_id"],
            "geom": coords_to_ewkt(update["coords"]),
            "altitude": json.dumps(update["altitude"]),
            "timestamps": json.dumps(update["timestamps"]),
            "modified": modified,
        },
    )


def _load_payloads(pg, trip_ids):
    """Read back whatever is currently stored for these trips, in client shape."""
    rows = pg.execute(
        """
        SELECT trip_id, altitude, timestamps, ST_AsGeoJSON(geom) AS geojson,
               EXTRACT(EPOCH FROM last_polled) AS updated
        FROM live_flight_tracks
        WHERE trip_id = ANY(CAST(:ids AS integer[])) AND geom IS NOT NULL
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
            "path": coords,
            "altitude": row["altitude"],
            "timestamps": row["timestamps"],
            # When this fix was actually taken. A stored row can be up to one refresh
            # interval old, so the client must not assume "now" — and it cannot derive
            # this from timestamps, which the altitude gate may have stripped.
            "updated": int(row["updated"]) if row["updated"] else None,
        }
    return payloads


def get_live_tracks(trip_ids):
    """Return {trip_id: {path, altitude, timestamps}} for whichever of trip_ids are
    in-progress, opted-in flights. Shared by the endpoint and by the /live_map server-side
    enrichment, so both surfaces get identical geometry and identical cost behaviour.

    Split into three phases so that no database session is ever held across an HTTP call:
    claim (short transaction), fetch (no session), store (short transaction).
    """
    with pg_session() as pg:
        trips = eligible_trips(pg, trip_ids)
        if not trips:
            return {}
        claimed = _claim_due_trips(pg, trips)

    owners = {
        u.uid: u
        for u in User.query.filter(User.uid.in_({t["user_id"] for t in trips})).all()
    }
    usernames = {uid: u.username for uid, u in owners.items()}

    updates = []
    for trip in claimed:
        username = usernames.get(trip["user_id"])
        if not username:
            continue
        try:
            updates.append((trip, _fetch_update(trip, username)))
        except Exception:
            # A live overlay is decoration: never let it break the map it sits on.
            logger.exception("Live track refresh failed for trip %s", trip["trip_id"])

    # One session per store: a failed statement aborts its whole transaction in Postgres,
    # so sharing one would let a single bad trip take down every other trip's write and
    # the final read as well.
    for trip, update in updates:
        try:
            with pg_session() as pg:
                _store_update(pg, trip, update)
        except Exception:
            logger.exception("Storing live track failed for trip %s", trip["trip_id"])

    with pg_session() as pg:
        payloads = _load_payloads(pg, [t["trip_id"] for t in trips])

    # The 3D altitude profile is separately opt-in per owner (see the show_3d gate on the
    # trip page): live tracking must not become a side door that exposes altitude for an
    # owner who only asked for their route to be drawn.
    owner_of = {t["trip_id"]: t["user_id"] for t in trips}
    for trip_id, payload in payloads.items():
        owner = owners.get(owner_of.get(trip_id))
        if not (owner and owner.premium and owner.flight_3d):
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
