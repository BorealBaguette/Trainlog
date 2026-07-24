"""
Re-align legacy flight tracks whose 2D geometry and 3D arrays drifted apart.

Old FR24 imports densified the 2D path with extra great-circle points but left the
altitude/timestamp arrays at the raw sample count, so ``paths.geom`` ends up with
more vertices than ``paths.altitude``/``paths.timestamps`` (they are meant to be
strictly 1:1, one value per vertex). The importer was later fixed to interpolate all
four components together; this script repairs the rows imported before that.

Fix: resample each mismatched array to the geometry's vertex count by linear
interpolation over normalized index. Because the extra geometry vertices were
inserted *between* the real samples, interpolation recovers a faithful altitude/time
at every vertex — at the original sample positions it reproduces the sample, and in
between it fills the gap smoothly. Timestamps stay monotonic.

Run ON THE SERVER (where POSTGRES_HOST resolves), dry-run first:

    docker compose exec trainlog python -m scripts.realign_flight_tracks
    docker compose exec trainlog python -m scripts.realign_flight_tracks --apply
"""

import argparse
import json
import logging

from src.pg import pg_session

logger = logging.getLogger(__name__)


def _resample(values, target_len):
    """Linearly resample `values` to `target_len` points over normalized index.

    None gaps fall back to the nearer known neighbour. Returns a new list; a list
    already at the target length is returned unchanged.
    """
    n = len(values)
    if n == target_len:
        return list(values)
    if n == 0:
        return [None] * target_len
    if n == 1:
        return [values[0]] * target_len

    out = []
    for i in range(target_len):
        f = i / (target_len - 1) * (n - 1)
        lo = int(f)
        hi = min(lo + 1, n - 1)
        frac = f - lo
        a, b = values[lo], values[hi]
        if a is None or b is None:
            out.append(a if a is not None else b)
        else:
            out.append(a + (b - a) * frac)
    return out


def _as_list(value):
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value


def find_and_fix(apply=False):
    """Report (and, with apply=True, repair) every path whose altitude or timestamp
    array length differs from its geometry vertex count.

    Returns a JSON-ready dict: {"applied", "count", "trips": [{trip_id, geom,
    altitude, timestamps}, ...]} where the array counts are the *original* lengths.
    """
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT trip_id,
                   ST_NPoints(geom) AS npts,
                   altitude,
                   timestamps,
                   jsonb_array_length(altitude)   AS nalt,
                   jsonb_array_length(timestamps) AS nts
            FROM paths
            WHERE (altitude IS NOT NULL
                   AND jsonb_array_length(altitude) <> ST_NPoints(geom))
               OR (timestamps IS NOT NULL
                   AND jsonb_array_length(timestamps) <> ST_NPoints(geom))
            ORDER BY trip_id
            """
        ).fetchall()

        trips = []
        for row in rows:
            tid = row["trip_id"]
            npts = row["npts"]
            trips.append(
                {
                    "trip_id": tid,
                    "geom": npts,
                    "altitude": row["nalt"],
                    "timestamps": row["nts"],
                }
            )
            if not apply:
                continue

            new_alt = new_ts = None
            alt = _as_list(row["altitude"])
            if alt is not None and len(alt) != npts:
                new_alt = [
                    round(v, 1) if isinstance(v, (int, float)) else v
                    for v in _resample(alt, npts)
                ]
            ts = _as_list(row["timestamps"])
            if ts is not None and len(ts) != npts:
                new_ts = [
                    int(round(v)) if isinstance(v, (int, float)) else v
                    for v in _resample(ts, npts)
                ]

            pg.execute(
                """
                UPDATE paths SET
                    altitude   = COALESCE(CAST(:altitude AS jsonb), altitude),
                    timestamps = COALESCE(CAST(:timestamps AS jsonb), timestamps)
                WHERE trip_id = :tid
                """,
                {
                    "tid": tid,
                    "altitude": json.dumps(new_alt) if new_alt is not None else None,
                    "timestamps": json.dumps(new_ts) if new_ts is not None else None,
                },
            )
            logger.info("Re-aligned trip %s to %s vertices", tid, npts)

    return {"applied": apply, "count": len(trips), "trips": trips}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the repaired arrays (default: dry run)"
    )
    args = parser.parse_args()
    result = find_and_fix(apply=args.apply)
    print(f"{'Applied to' if result['applied'] else 'Dry run — found'} {result['count']} path(s).")
    for t in result["trips"]:
        print(f"  trip {t['trip_id']}: geom={t['geom']} altitude={t['altitude']} timestamps={t['timestamps']}")
    if not result["applied"]:
        print("Re-run with --apply to repair.")
