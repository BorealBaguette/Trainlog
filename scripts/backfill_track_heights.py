"""
Re-read the heights of rail paths from Mapterhorn (src/terrain.py).

Rail trips saved before the switch carry the router's heights: a surface model (SRTM,
tree tops and roofs), several metres above the bare ground the 3D trip view draws, and
coarse in the mountains. New saves read the ground itself (src/track_profile.py,
track_from_route); this brings the stored ones in line. Only paths with tunnel/bridge
stretches (track_env), the ones the 3D view shows, are touched; their geometry is not.

Run ON THE SERVER (where POSTGRES_HOST resolves), dry-run first:

    docker compose exec trainlog python -m scripts.backfill_track_heights
    docker compose exec trainlog python -m scripts.backfill_track_heights --apply
    docker compose exec trainlog python -m scripts.backfill_track_heights --apply --trip 992487 992488
"""

import argparse
import json
import logging

from src.paths import geom_geojson_to_coords
from src.pg import pg_session
from src.terrain import ground_heights
from src.track_profile import TRACK_TYPES

logger = logging.getLogger(__name__)


def backfill(apply=False, trip_ids=None):
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT p.trip_id, ST_AsGeoJSON(p.geom) AS geojson
            FROM paths p JOIN trips t ON t.trip_id = p.trip_id
            WHERE p.track_env IS NOT NULL AND t.trip_type = ANY(:types)
              AND (CAST(:ids AS int[]) IS NULL OR p.trip_id = ANY(CAST(:ids AS int[])))
            ORDER BY p.trip_id
            """,
            {"types": list(TRACK_TYPES), "ids": trip_ids},
        ).fetchall()
        done = skipped = 0
        for row in rows:
            heights = ground_heights(geom_geojson_to_coords(row["geojson"]))
            if not any(v is not None for v in heights):
                skipped += 1
                logger.warning("trip %s: no terrain read, left as it is", row["trip_id"])
                continue
            done += 1
            if apply:
                pg.execute(
                    "UPDATE paths SET altitude = CAST(:altitude AS jsonb) WHERE trip_id = :tid",
                    {"altitude": json.dumps(heights), "tid": row["trip_id"]},
                )
        print(f"{len(rows)} paths, {done} {'updated' if apply else 'to update'}, {skipped} without terrain")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the heights (default: dry run)")
    parser.add_argument("--trip", type=int, nargs="+", help="only these trip ids")
    args = parser.parse_args()
    backfill(apply=args.apply, trip_ids=args.trip)
