"""
Export how many Trainlog users have used each quai station, for quai's search to put the
stations people use first among those matching a search alike (quai's usage.py loads it).

Every trip's two ends of a quai mode are tied to a station the way the tidy-stations page
does it (src/station_cleanup.py): the station the trip was saved at, else the one named
like the end among those nearest it. An end only near a station, not named like it, counts
for none: it would boost stations by place alone.

Counted in distinct users, not trips: one commuter's two thousand trips should not lift
their village halt over a city's main station. And only stations at least MIN_USERS people
have used: below that a count tells where someone travels, and lifts nothing worth it.

Run (the file then goes to quai: make usage USAGE=usage.csv):
    docker compose exec trainlog python -m scripts.export_station_usage --out usage.csv
"""

import argparse
import csv
import logging
import sys
import time

from src.pg import pg_session
from src.quai import QUAI_MODES, nearest_stations
from src.station_cleanup import CANDIDATES, END_RADIUS_M, station_of_end

logger = logging.getLogger(__name__)

MIN_USERS = 3

# Every trip's two ends, with its name and station key there, and who made it; points
# rounded to about 100m, so that the many trips from one station take a single lookup.
ENDS_SQL = """
SELECT t.trip_type, e.label, e.station_key,
       round(ST_Y(e.point)::numeric, 3)::float8 AS lat,
       round(ST_X(e.point)::numeric, 3)::float8 AS lng,
       array_agg(DISTINCT t.user_id) AS user_ids,
       count(*) AS trips
FROM trips t
JOIN paths p ON p.trip_id = t.trip_id
CROSS JOIN LATERAL (VALUES
    (t.origin_station, t.origin_station_key, ST_StartPoint(ST_GeometryN(p.geom, 1))),
    (t.destination_station, t.destination_station_key,
     ST_EndPoint(ST_GeometryN(p.geom, ST_NumGeometries(p.geom))))
) AS e(label, station_key, point)
WHERE t.trip_type = ANY(:types) AND e.point IS NOT NULL
GROUP BY 1, 2, 3, 4, 5
"""


def station_usage(min_users=MIN_USERS):
    """[(mode, station_key, users, trips)] for the stations at least `min_users` people have
    used, the most used first; and how many trip ends were tied to none."""
    started = time.monotonic()
    with pg_session() as pg:
        ends = pg.execute(ENDS_SQL, {"types": list(QUAI_MODES)}).fetchall()
    logger.info(f"{len(ends)} distinct ends read in {time.monotonic() - started:.0f}s")

    by_mode = {}
    for end in ends:
        by_mode.setdefault(QUAI_MODES[end.trip_type], []).append(end)

    users, trips, untied = {}, {}, 0
    for mode, mode_ends in by_mode.items():
        started = time.monotonic()
        points = sorted({(end.lat, end.lng) for end in mode_ends})
        at = dict(zip(points, nearest_stations(mode, [list(p) for p in points], END_RADIUS_M,
                                               candidates=CANDIDATES, timeout=300)))
        for end in mode_ends:
            nearby = at[(end.lat, end.lng)]
            station, alike = station_of_end(end.label, end.station_key, nearby) if nearby else (None, False)
            if not alike:
                untied += end.trips
                continue
            key = (mode, station["station_key"])
            users.setdefault(key, set()).update(end.user_ids)
            trips[key] = trips.get(key, 0) + end.trips
        logger.info(f"{mode}: {len(points)} points looked up in {time.monotonic() - started:.0f}s")

    rows = [(mode, key, len(ids), trips[(mode, key)])
            for (mode, key), ids in users.items() if len(ids) >= min_users]
    rows.sort(key=lambda row: (-row[2], -row[3], row[0], row[1]))
    return rows, untied


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--out", help="the CSV to write (default: standard output)")
    parser.add_argument("--min-users", type=int, default=MIN_USERS,
                        help=f"leave out stations fewer people used (default {MIN_USERS})")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    rows, untied = station_usage(args.min_users)
    out = open(args.out, "w", newline="") if args.out else sys.stdout
    try:
        writer = csv.writer(out)
        writer.writerow(["mode", "station_key", "users", "trips"])
        writer.writerows(rows)
    finally:
        if args.out:
            out.close()
    logger.info(f"{len(rows)} stations used by at least {args.min_users} people; "
                f"{untied} trip ends tied to no station")


if __name__ == "__main__":
    main()
