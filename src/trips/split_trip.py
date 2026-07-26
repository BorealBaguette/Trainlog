"""
Split one trip into two at a chosen path node, keeping the 3D flight track intact.

The route/import tooling carries geom but not the per-vertex altitude/timestamps
arrays, so a naive duplicate-then-trim loses them. This module slices all three
parallel arrays (geom, altitude, timestamps) at a node the user picks on the map:

  * leg 1 reuses the original trip row (destination ← the mid station name);
  * leg 2 is a brand-new trip (origin ← the same mid station name).

The clicked node belongs to both legs (leg 1 ends there, leg 2 starts there), so no
sample is dropped. Every derived field (length, countries, carbon, duration, the cut
datetime) is recomputed per leg with the same helpers trip creation/editing use, so
each half is indistinguishable from a trip logged in one piece. The two outer
datetimes are preserved exactly; only the middle boundary is derived from the node's
timestamp, using the original trip's local/UTC offset.
"""

import datetime
import json
import logging

from py.utils import getCountriesFromPath, getDistance
from src.carbon import calculate_carbon_footprint_for_trip
from src.operators import sync_trip_operators
from src.paths import coords_to_ewkt, geom_geojson_to_coords
from src.pg import pg_session
from src.sql.trips import insert_trip_query, update_trip_query

logger = logging.getLogger(__name__)


def _as_list(value):
    """jsonb columns come back as a Python list already, but tolerate a JSON string."""
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def _has_flag_prefix(s):
    """True if `s` begins with a flag emoji (a Unicode regional-indicator symbol)."""
    return bool(s) and 0x1F1E6 <= ord(s[0]) <= 0x1F1FF


def _path_length(coords):
    """Great-circle length (metres) of a [[lat, lng], ...] line."""
    total = 0.0
    for i in range(1, len(coords)):
        total += getDistance(
            {"lat": coords[i - 1][0], "lng": coords[i - 1][1]},
            {"lat": coords[i][0], "lng": coords[i][1]},
        )
    return total


def get_split_data(pg, trip_id):
    """Return the trip row plus its per-node track for the splitter UI, or None.

    nodes = [{"i", "lat", "lng", "alt", "ts"}] aligned with the geom vertices.
    """
    trip = pg.execute(
        "SELECT * FROM trips WHERE trip_id = :tid", {"tid": trip_id}
    ).fetchone()
    if trip is None:
        return None
    path = pg.execute(
        "SELECT ST_AsGeoJSON(geom) AS geojson, altitude, timestamps"
        " FROM paths WHERE trip_id = :tid",
        {"tid": trip_id},
    ).fetchone()
    if path is None:
        return {"trip": dict(trip._mapping), "nodes": []}

    coords = geom_geojson_to_coords(path["geojson"])
    altitude = _as_list(path["altitude"]) or []
    timestamps = _as_list(path["timestamps"]) or []
    nodes = []
    for i, (lat, lng) in enumerate(coords):
        nodes.append(
            {
                "i": i,
                "lat": lat,
                "lng": lng,
                "alt": altitude[i] if i < len(altitude) else None,
                "ts": timestamps[i] if i < len(timestamps) else None,
            }
        )
    return {"trip": dict(trip._mapping), "nodes": nodes}


def _leg_datetimes(trip, timestamps, split_index):
    """Compute (leg1_end, leg2_start) local + UTC datetimes at the cut.

    The cut node's UTC instant comes from its epoch timestamp; the local time is
    derived by applying the original trip's local↔UTC offset. Returns four datetimes
    (leg1 local end, leg1 utc end, = leg2 starts) or (None, None) if the trip has no
    real datetimes / timestamps to split on.
    """
    ts = timestamps[split_index] if split_index < len(timestamps) else None
    start_local = trip["start_datetime"]
    start_utc = trip["utc_start_datetime"]
    if ts is None or not isinstance(start_local, datetime.datetime):
        return None, None
    # A trip only displays clock times when start/end land on whole minutes
    # (formatTrip hides them when seconds != 0). Raw epoch timestamps carry seconds,
    # so round the cut to the nearest minute — and derive the local time from the
    # rounded UTC so the two stay exactly offset-consistent.
    cut_utc = datetime.datetime.utcfromtimestamp(int(ts))
    cut_utc = (cut_utc + datetime.timedelta(seconds=30)).replace(
        second=0, microsecond=0
    )
    if isinstance(start_utc, datetime.datetime):
        offset = start_local - start_utc
    else:
        offset = datetime.timedelta(0)
    cut_local = cut_utc + offset
    return cut_local, cut_utc


def _recompute(trip, coords, trip_type):
    """(trip_length, countries_json, carbon) for a leg's coordinate slice."""
    length = _path_length(coords)
    path_dicts = [{"lat": c[0], "lng": c[1]} for c in coords]
    countries = getCountriesFromPath(
        path_dicts, trip_type, powerType=(trip["power_type"] or None)
    )
    carbon = calculate_carbon_footprint_for_trip(
        {
            "type": trip_type,
            "trip_length": length,
            "countries": countries,
            "start_datetime": trip["start_datetime"],
            "power_type": trip["power_type"],
            "material_type": trip["material_type"],
            "co2_override": trip["co2_override"],
        },
        coords,
    )
    return length, countries, carbon


def _write_path(pg, trip_id, coords, altitude, timestamps):
    """Insert/replace a paths row with geom + the sliced 3D arrays."""
    ewkt = coords_to_ewkt(coords)
    if ewkt is None:
        return
    pg.execute(
        "INSERT INTO paths (trip_id, geom, altitude, timestamps)"
        " VALUES (:tid, ST_GeomFromEWKT(:ewkt),"
        " CAST(:altitude AS jsonb), CAST(:timestamps AS jsonb))"
        " ON CONFLICT (trip_id) DO UPDATE SET geom = EXCLUDED.geom,"
        " altitude = EXCLUDED.altitude, timestamps = EXCLUDED.timestamps",
        {
            "tid": trip_id,
            "ewkt": ewkt,
            "altitude": json.dumps(altitude) if altitude else None,
            "timestamps": json.dumps(timestamps) if timestamps else None,
        },
    )


def split_trip(trip_id, split_index, mid_station, user_id):
    """Split trip `trip_id` at node `split_index` into two legs joined at `mid_station`.

    `user_id` must own the trip. Returns {"leg1_id", "leg2_id"}. Raises ValueError on
    bad input (unknown trip, wrong owner, index that would leave a <2-point leg).
    """
    mid_station = (mid_station or "").strip()
    if not mid_station:
        raise ValueError("A station name for the split point is required")
    # Stations are stored flag-first (e.g. "🇮🇹 Vieste"); a regional-indicator pair
    # leads the string. Refuse a bare name so no flagless station is ever recorded.
    if not _has_flag_prefix(mid_station):
        raise ValueError("The split station must start with a country flag")

    with pg_session() as pg:
        data = get_split_data(pg, trip_id)
        if data is None:
            raise ValueError(f"Trip {trip_id} not found")
        trip = data["trip"]
        if trip["user_id"] != user_id:
            raise ValueError("You do not own this trip")

        nodes = data["nodes"]
        n = len(nodes)
        # Each leg keeps the cut node, so both need at least one further node.
        if not (1 <= split_index <= n - 2):
            raise ValueError(
                f"Split index {split_index} out of range for a {n}-node path"
            )

        coords = [[nd["lat"], nd["lng"]] for nd in nodes]
        altitude = [nd["alt"] for nd in nodes]
        timestamps = [nd["ts"] for nd in nodes]
        has_alt = any(a is not None for a in altitude)
        has_ts = any(t is not None for t in timestamps)

        # Inclusive slices sharing the cut node.
        c1, c2 = coords[: split_index + 1], coords[split_index:]
        a1 = altitude[: split_index + 1] if has_alt else None
        a2 = altitude[split_index:] if has_alt else None
        t1 = timestamps[: split_index + 1] if has_ts else None
        t2 = timestamps[split_index:] if has_ts else None

        trip_type = trip["trip_type"]
        cut_local, cut_utc = _leg_datetimes(trip, timestamps, split_index)
        now = datetime.datetime.now()

        len1, cty1, co2_1 = _recompute(trip, c1, trip_type)
        len2, cty2, co2_2 = _recompute(trip, c2, trip_type)

        def _dur(start_utc, end_utc):
            if isinstance(start_utc, datetime.datetime) and isinstance(
                end_utc, datetime.datetime
            ):
                return int((end_utc - start_utc).total_seconds())
            return trip["estimated_trip_duration"]

        # ── Leg 1: reuse the original row, now ending at the cut. ──
        pg.execute(
            update_trip_query(),
            {
                "trip_id": trip_id,
                "origin_station": trip["origin_station"],
                "destination_station": mid_station,
                "start_datetime": trip["start_datetime"],
                "end_datetime": cut_local or trip["end_datetime"],
                "is_project": trip["is_project"],
                "utc_start_datetime": trip["utc_start_datetime"],
                "utc_end_datetime": cut_utc or trip["utc_end_datetime"],
                "estimated_trip_duration": _dur(trip["utc_start_datetime"], cut_utc),
                "manual_trip_duration": trip["manual_trip_duration"],
                "trip_length": len1,
                "operator": trip["operator"],
                "countries": cty1,
                "line_name": trip["line_name"],
                "created": trip["created"],
                "last_modified": now,
                "trip_type": trip_type,
                "material_type": trip["material_type"],
                "material_type_advanced": trip["material_type_advanced"],
                "seat": trip["seat"],
                "reg": trip["reg"],
                # A sliced route's stored waypoints no longer describe it.
                "waypoints": None,
                "notes": trip["notes"],
                "price": trip["price"],
                "currency": trip["currency"],
                "ticket_id": trip["ticket_id"],
                "purchase_date": trip["purchase_date"],
                "carbon": co2_1,
                "visibility": trip["visibility"],
                "departure_delay": trip["departure_delay"],
                # The cut is an internal boundary — no schedule to be late against.
                "arrival_delay": None,
                "power_type": trip["power_type"],
                "co2_override": trip["co2_override"],
                "route_source": trip["route_source"],
            },
        )
        _write_path(pg, trip_id, c1, a1, t1)
        sync_trip_operators(trip_id, pg_session_=pg)

        # ── Leg 2: a new trip starting at the cut. Price/ticket stay on leg 1 only. ──
        leg2_id = pg.execute(
            insert_trip_query(),
            {
                "user_id": trip["user_id"],
                "origin_station": mid_station,
                "destination_station": trip["destination_station"],
                "start_datetime": cut_local or trip["start_datetime"],
                "end_datetime": trip["end_datetime"],
                "is_project": trip["is_project"],
                "utc_start_datetime": cut_utc or trip["utc_start_datetime"],
                "utc_end_datetime": trip["utc_end_datetime"],
                "estimated_trip_duration": _dur(cut_utc, trip["utc_end_datetime"]),
                "manual_trip_duration": None,
                "trip_length": len2,
                "operator": trip["operator"],
                "countries": cty2,
                "line_name": trip["line_name"],
                "created": now,
                "last_modified": now,
                "trip_type": trip_type,
                "material_type": trip["material_type"],
                "material_type_advanced": trip["material_type_advanced"],
                "seat": trip["seat"],
                "reg": trip["reg"],
                "waypoints": None,
                "notes": trip["notes"],
                "price": None,
                "currency": None,
                "ticket_id": None,
                "purchase_date": None,
                "carbon": co2_2,
                "visibility": trip["visibility"],
                "departure_delay": None,
                "arrival_delay": trip["arrival_delay"],
                "power_type": trip["power_type"],
                "co2_override": trip["co2_override"],
                "route_source": trip["route_source"],
            },
        ).fetchone()[0]
        _write_path(pg, leg2_id, c2, a2, t2)
        sync_trip_operators(leg2_id, pg_session_=pg)

    logger.info(
        "Split trip %s at node %s -> leg1 %s + leg2 %s",
        trip_id,
        split_index,
        trip_id,
        leg2_id,
    )
    return {"leg1_id": trip_id, "leg2_id": leg2_id}
