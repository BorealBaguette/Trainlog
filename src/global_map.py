"""Pre-aggregated hexagon bins for the global deck.gl page.

Every relevant trip is reduced to points carrying its owner's user id and trip
type, binned into a fixed flat-top hexagon grid in Web Mercator (arithmetic, no
ST_Transform / grid-join — far too slow over ~860M path vertices). The expensive
point dump is done ONCE into an unlogged table keyed by (hexagon, trip_type,
user_id); each named bin is then a cheap slice of that table filtered to its trip
types and to hexagons touched by >= 3 distinct users.

Datasets (each its own .bin, all enforcing the >= 3-users rule):
  * land   - everything except air/helicopter/ferry (default)
  * rail   - train, tram, metro, funicular, rail
  * road   - bus, car, cycle, scooter, walk
  * air    - air + helicopter (full flight paths, not just endpoints)
  * <type> - one bin per individual trip type

Non-air trips are simplified+segmentized+snapped (uniform sampling along the
path); air/helicopter are only simplified+snapped (segmentizing a 2-point flight
would densify a straight rhumb line into millions of points).

Each .bin is little-endian float32 triplets: hex-centre lng, lat, weight.
The build is expensive (minutes); files are cached and only rebuilt on demand.
"""

import json
import logging
import os
import threading
import time
from array import array
from datetime import UTC, datetime

from src.pg import pg_session

logger = logging.getLogger(__name__)

CACHE_DIR = "databases/cache"

# Hexagon circumradius (centre-to-corner) in Web Mercator metres.
HEX_SIZE = 15000.0
# Minimum distinct users for a hexagon to be shown.
MIN_USERS = 3

# All trip types present in the DB.
TRIP_TYPES = [
    "train", "tram", "metro", "funicular", "rail",
    "bus", "car", "cycle", "scooter", "walk",
    "ferry", "air", "helicopter", "aerialway", "ski",
    "accommodation", "restaurant", "poi",
]

RAIL_TYPES = ["train", "tram", "metro", "funicular", "rail"]
ROAD_TYPES = ["bus", "car", "cycle", "scooter", "walk"]
AIR_TYPES = ["air", "helicopter"]
LAND_EXCLUDE = ["air", "helicopter", "ferry"]

_TYPE_LABELS = {"poi": "POI", "aerialway": "Aerial way"}


def _type_label(t):
    return _TYPE_LABELS.get(t, t.capitalize())


# Ordered bin catalogue: name -> (label, include_types|None, exclude_types|None).
def _bin_catalogue():
    bins = [
        ("land", "All land-based", None, LAND_EXCLUDE),
        ("rail", "All rail", RAIL_TYPES, None),
        ("road", "All road", ROAD_TYPES, None),
        ("air", "Air + helicopter", AIR_TYPES, None),
    ]
    for t in TRIP_TYPES:
        bins.append((f"type_{t}", _type_label(t), [t], None))
    return bins


BINS = _bin_catalogue()
BIN_NAMES = {b[0] for b in BINS}
DEFAULT_BIN = "land"

_build_lock = threading.Lock()


def cache_file(name):
    return os.path.join(CACHE_DIR, f"global_hex_{name}.bin")


def available_bins():
    """[{value, label}, ...] for the dataset dropdown.

    Only datasets whose .bin exists and is non-empty are listed, so empty types
    (e.g. scooter/ski under the >=3-users rule) disappear from the UI. Before any
    build has run (fresh deploy) nothing exists yet, so fall back to the full list
    so the page still works and the default bin can build lazily.
    """
    built = [
        b for b in BINS
        if os.path.exists(cache_file(b[0])) and os.path.getsize(cache_file(b[0])) > 0
    ]
    chosen = built or BINS
    return [{"value": b[0], "label": b[1]} for b in chosen]


_HEAVY_SQL = """
CREATE UNLOGGED TABLE hex_agg_tmp AS
WITH base AS (
    SELECT t.user_id, t.trip_type, p.geom
    FROM paths p JOIN trips t ON t.trip_id = p.trip_id
    WHERE NOT (t.is_project AND t.start_datetime IS NULL)
),
pts AS (
    SELECT user_id, trip_type,
           (ST_DumpPoints(ST_SnapToGrid(ST_Segmentize(ST_Simplify(geom, 0.01), 0.02), 0.02))).geom AS g
    FROM base WHERE trip_type NOT IN ('air', 'helicopter')
    UNION ALL
    SELECT user_id, trip_type,
           (ST_DumpPoints(ST_SnapToGrid(ST_Simplify(geom, 0.01), 0.02))).geom AS g
    FROM base WHERE trip_type IN ('air', 'helicopter')
),
merc AS (
    SELECT user_id, trip_type,
           ST_X(g) * 6378137.0 * pi() / 180.0 AS x,
           ln(tan(pi() / 4 + ST_Y(g) * pi() / 360.0)) * 6378137.0 AS y
    FROM pts WHERE g IS NOT NULL AND ST_Y(g) BETWEEN -85 AND 85
),
ax AS (
    SELECT user_id, trip_type,
           (2.0 / 3.0 * x) / :size AS qf,
           ((-1.0 / 3.0) * x + sqrt(3.0) / 3.0 * y) / :size AS rf
    FROM merc
),
rnd AS (
    SELECT user_id, trip_type, qf, rf,
        round(qf) AS rx, round(-qf - rf) AS ry, round(rf) AS rz,
        abs(round(qf) - qf) AS dx,
        abs(round(-qf - rf) - (-qf - rf)) AS dy,
        abs(round(rf) - rf) AS dz
    FROM ax
),
hexed AS (
    SELECT user_id, trip_type,
        CASE WHEN dx > dy AND dx > dz THEN -ry - rz ELSE rx END AS qi,
        CASE WHEN (dx > dy AND dx > dz) OR (dy > dz) THEN rz ELSE -rx - ry END AS ri
    FROM rnd
)
SELECT qi, ri, trip_type, user_id, count(*)::int AS cnt
FROM hexed GROUP BY qi, ri, trip_type, user_id
"""

_BIN_SQL = """
SELECT
    (:size * 1.5 * qi) / 6378137.0 * 180.0 / pi() AS lng,
    (2.0 * atan(exp((:size * sqrt(3.0) * (ri + qi / 2.0)) / 6378137.0)) - pi() / 2.0)
        * 180.0 / pi() AS lat,
    sum(cnt) AS weight
FROM hex_agg_tmp
WHERE {cond}
GROUP BY qi, ri
HAVING count(DISTINCT user_id) >= :min_users
"""


def _write_bin(name, rows):
    buf = array("f")
    for r in rows:
        buf.append(r["lng"])
        buf.append(r["lat"])
        buf.append(float(r["weight"]))
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = cache_file(name) + ".tmp"
    with open(tmp, "wb") as f:
        buf.tofile(f)
    os.replace(tmp, cache_file(name))
    return len(rows)


# Build status + history live in a JSON file in the cache, so progress survives a
# restart and is visible from whichever gunicorn worker the admin poll lands on.
STATUS_FILE = os.path.join(CACHE_DIR, "global_hex_status.json")
_status_lock = threading.Lock()
_MAX_DURATIONS = 5  # keep the last few build times for the estimate


def build_status():
    """Snapshot of the last/current build (from the on-disk status file)."""
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"running": False, "phase": None, "durations": []}


def _set_status(**updates):
    with _status_lock:
        status = build_status()
        status.update(updates)
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = STATUS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(status, f)
        os.replace(tmp, STATUS_FILE)


def build_all():
    """Build the shared hex table once, then write every bin's .bin file.

    Reports progress (phase + how many bins are done) and records how long the
    build took so the admin UI can show an estimate next time.
    """
    with _build_lock:
        logger.info("Building bestagons bins (this takes a few minutes)...")
        t0 = time.monotonic()
        prev = build_status().get("durations", [])
        _set_status(
            running=True,
            phase="aggregating",
            started=datetime.now(UTC).isoformat(timespec="seconds"),
            finished=None,
            error=None,
            bins_done=0,
            bins_total=len(BINS),
            current=None,
            counts={},
            durations=prev,
            estimate_seconds=(prev[-1] if prev else None),
        )

        counts = {}
        try:
            with pg_session() as pg:
                pg.execute("DROP TABLE IF EXISTS hex_agg_tmp")
                pg.execute(_HEAVY_SQL, {"size": HEX_SIZE})
                pg.execute("CREATE INDEX ON hex_agg_tmp (trip_type)")

                _set_status(phase="writing")
                for i, (name, _label, include, exclude) in enumerate(BINS):
                    params = {"size": HEX_SIZE, "min_users": MIN_USERS}
                    if include is not None:
                        cond = "trip_type = ANY(:types)"
                        params["types"] = include
                    else:
                        cond = "trip_type <> ALL(:types)"
                        params["types"] = exclude
                    rows = pg.execute(_BIN_SQL.format(cond=cond), params).fetchall()
                    counts[name] = _write_bin(name, rows)
                    _set_status(bins_done=i + 1, current=name, counts=counts)
                    logger.info("  bin %s: %d hexagons", name, counts[name])

                pg.execute("DROP TABLE hex_agg_tmp")
        except Exception as e:  # noqa: BLE001 - surfaced to the admin UI
            logger.exception("Bestagons build failed")
            _set_status(
                running=False, phase="error", error=str(e),
                finished=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            return counts

        duration = round(time.monotonic() - t0, 1)
        durations = (prev + [duration])[-_MAX_DURATIONS:]
        _set_status(
            running=False,
            phase="done",
            finished=datetime.now(UTC).isoformat(timespec="seconds"),
            counts=counts,
            duration_seconds=duration,
            durations=durations,
            estimate_seconds=duration,
        )
        logger.info("Bestagons bins built in %ss.", duration)
        return counts


def build_all_async():
    """Kick off a build in a background thread. Returns False if one is running."""
    if build_status().get("running"):
        return False
    threading.Thread(target=build_all, daemon=True).start()
    return True


def get_cache_path(name=DEFAULT_BIN):
    """Return the .bin path for a dataset (no build — see build_all_async)."""
    if name not in BIN_NAMES:
        name = DEFAULT_BIN
    return cache_file(name)
