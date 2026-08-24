"""
Resolve historical trip endpoints against the station registry.

Trips store only a label, so the years of existing trips have no station attached. Two
passes recover what can be recovered, in descending order of confidence, and the rest
becomes the admin queue.

  1. Text     exact match of the spelling against station_aliases. Free, and run by
              rebuild_labels() itself — this script only reports it.

  2. Geometry the identity everyone assumed was lost. `trips.origin_station` dropped the
              coordinates, but `paths.geom` still holds the route, so ST_StartPoint and
              ST_EndPoint give a real position for both endpoints of every routed trip.
              An unresolved label whose endpoint sits on top of a registered station, and
              whose text is recognisably that station's, is that station.

  3. Nothing  left unresolved, ordered by occurrence count for a human to look at.

── Why pass 2 writes aliases rather than station ids ──────────────────────────────────────
`station_labels` is purely derived: rebuild_labels() re-resolves every spelling from the
registry, and that property is what makes the whole design safe to re-run. A geometry match
written straight into `station_labels.station_id` would be erased by the next rebuild,
because nothing in the registry says where it came from.

So a geometry match instead records the label as an *alias* of the station it matched. The
knowledge then lives in the registry, where it is durable, and the ordinary text resolution
picks it up on every rebuild. It also makes that spelling searchable, and it fixes every
other trip using the same label at the same time — including trips with no route geometry
of their own, which pass 2 could never have reached directly.

Usage:
    python3 scripts/backfill_stations.py --report          # measure, change nothing
    python3 scripts/backfill_stations.py --geometry        # run pass 2 and resync
    python3 scripts/backfill_stations.py --geometry --limit 500 --dry-run
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts._env import load_env  # noqa: E402

# Reads .env and picks a database host that resolves from wherever this is run,
# so the script works both on the server and from a local shell.
load_env()

from src.pg import init_db_engine, pg_session  # noqa: E402
from src.stations import (  # noqa: E402
    rebuild_labels,
    registry_stats,
    unresolved_labels,
)

# How close a trip's path endpoint must be to a registered station to be considered the same
# place. Generous enough for a large terminus, where the routing engine's start point can sit
# several hundred metres from the station node it was snapped from, and far below the spacing
# of two distinct stations that share a name.
MATCH_RADIUS_M = 1500

# How alike the trip's label and the station's known spellings must also be. Distance alone
# is not enough: in a dense city several stations fall inside the radius, and matching on
# proximity only would attribute a trip to whichever happened to be nearest.
MIN_NAME_SIMILARITY = 0.45

# Candidate aliases found by geometry, before they are written.
GEOMETRY_MATCH_SQL = """
WITH unresolved AS (
    SELECT sl.label_id, sl.normalized, sl.station_type, sl.sample_label
    FROM station_labels sl
    WHERE sl.station_id IS NULL AND sl.occurrences > 0
    ORDER BY sl.occurrences DESC
    LIMIT :limit
),
-- The coordinates the label lost. PostGIS keeps the route, so its first and last vertex are
-- the origin and destination of every trip that used this spelling. Several trips share a
-- spelling, and they should agree on where it is — so each contributes a point and the match
-- has to hold across them.
located AS (
    SELECT u.sample_label AS raw_name,
           CASE WHEN station_normalize(t.origin_station) = u.normalized
                THEN ST_StartPoint(ST_GeometryN(p.geom, 1))
                ELSE ST_EndPoint(ST_GeometryN(p.geom, ST_NumGeometries(p.geom)))
           END AS point
    FROM unresolved u
    JOIN trips t
      ON station_type_bucket(t.trip_type) = u.station_type
     AND (station_normalize(t.origin_station) = u.normalized
          OR station_normalize(t.destination_station) = u.normalized)
    JOIN paths p ON p.trip_id = t.trip_id
),
-- The best registered station near each endpoint, with how well the names agree.
candidates AS (
    SELECT l.raw_name,
           s.station_id,
           ST_Distance(l.point::geography, ST_MakePoint(s.effective_lng, s.effective_lat)::geography) AS metres,
           (
               SELECT max(similarity(station_fold(a.alias), station_fold(l.raw_name)))
               FROM station_aliases a WHERE a.station_id = s.station_id
           ) AS name_score
    FROM located l
    JOIN stations s
      ON s.superseded_by IS NULL
     AND s.effective_lat IS NOT NULL
     AND ST_DWithin(l.point::geography, ST_MakePoint(s.effective_lng, s.effective_lat)::geography, :radius)
)
SELECT raw_name,
       station_id,
       min(metres) AS metres,
       max(name_score) AS name_score,
       count(*) AS occurrences
FROM candidates
WHERE name_score >= :min_similarity
GROUP BY raw_name, station_id
-- One label must not become an alias of two different stations: that would make every trip
-- using it ambiguous and so resolve to nothing, which is worse than leaving it alone.
HAVING count(DISTINCT station_id) = 1
ORDER BY count(*) DESC
"""


def report():
    stats = registry_stats()
    total = stats["endpoints"] or 1
    print("Registry")
    print(f"  stations             {stats['stations']:>10,}")
    print(f"  awaiting enrichment  {stats['awaiting_enrichment']:>10,}")
    print(f"  aliases              {stats['aliases']:>10,}")
    print("\nTrip endpoints")
    print(f"  total                {stats['endpoints']:>10,}")
    print(
        f"  resolved             {stats['resolved']:>10,}"
        f"  ({100.0 * stats['resolved'] / total:.1f}%)"
    )
    print(f"  distinct unresolved  {stats['unresolved_labels']:>10,}")

    top = unresolved_labels(limit=15)
    if top:
        print("\nMost-used unresolved labels (the admin queue)")
        for row in top:
            print(
                f"  {row['occurrences']:>7,}  {row['users']:>4} users  "
                f"{row['country'] or '  '}  {row['raw_name']}"
            )


def geometry_pass(limit, dry_run):
    """Pass 2: recover identity from the route geometry, recording it as aliases."""
    from src.stations import add_aliases, resync_station

    with pg_session() as pg:
        rows = pg.execute(
            GEOMETRY_MATCH_SQL,
            {
                "limit": limit,
                "radius": MATCH_RADIUS_M,
                "min_similarity": MIN_NAME_SIMILARITY,
            },
        ).fetchall()

    print(f"Geometry pass: {len(rows)} label(s) matched a station\n")
    for row in rows[:25]:
        print(
            f"  {row['occurrences']:>6,}x  {row['metres']:>7.0f}m  "
            f"sim {row['name_score']:.2f}  {row['raw_name']}  ->  station {row['station_id']}"
        )
    if len(rows) > 25:
        print(f"  … and {len(rows) - 25} more")

    if dry_run:
        print("\n--dry-run: nothing written.")
        return

    written = set()
    for row in rows:
        # kind='alias' rather than a name kind: this spelling is how users write the place,
        # not something OSM calls it.
        add_aliases(row["station_id"], [(row["raw_name"], "alias", None)])
        written.add(row["station_id"])

    print(f"\nWrote aliases on {len(written)} station(s); re-resolving affected trips…")
    resynced = sum(resync_station(station_id) for station_id in written)
    print(f"Re-synced {resynced} trip(s).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="measure only")
    parser.add_argument("--geometry", action="store_true", help="run the geometry pass")
    parser.add_argument(
        "--rebuild", action="store_true", help="re-derive and re-resolve every spelling first"
    )
    parser.add_argument("--limit", type=int, default=2_000,
                        help="how many unresolved spellings to consider")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_db_engine()

    if args.rebuild:
        print("Rebuilding the label cache from trip text…")
        print(f"  {rebuild_labels():,} distinct spellings\n")

    if args.geometry:
        geometry_pass(args.limit, args.dry_run)
        print()

    report()


if __name__ == "__main__":
    main()
