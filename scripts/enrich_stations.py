"""Drain the station enrichment queue by hand, e.g. after a bulk import. The background
enricher (src/station_osm.start_station_enricher) normally does this.

    python3 scripts/enrich_stations.py --max-batches 20
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts._env import load_env  # noqa: E402

load_env()

from src.pg import init_db_engine  # noqa: E402
from src.station_osm import drain_enrichment_queue  # noqa: E402
from src.stations import registry_stats  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-batches",
        type=int,
        default=5,
        help="how many 60-station batches to fetch in this run (default 5)",
    )
    parser.add_argument(
        "--no-objects",
        action="store_true",
        help="skip the second query that maps a station's other OSM objects",
    )
    parser.add_argument("--quiet", action="store_true", help="only report on failure")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    init_db_engine()

    before = registry_stats()
    if not before["awaiting_enrichment"]:
        if not args.quiet:
            print("Nothing awaiting enrichment.")
        return 0

    result = drain_enrichment_queue(
        max_batches=args.max_batches, map_objects=not args.no_objects
    )
    after = registry_stats()

    if not args.quiet or result["failed_batches"]:
        print(
            f"Enriched {result['enriched']} station(s) in {result['batches']} batch(es); "
            f"mapped {result['objects_mapped']} OSM object(s). "
            f"{after['awaiting_enrichment']} still queued."
        )
        if result["failed_batches"]:
            print(f"{result['failed_batches']} batch(es) failed and stay queued.")

    return 1 if result["failed_batches"] else 0


if __name__ == "__main__":
    sys.exit(main())
