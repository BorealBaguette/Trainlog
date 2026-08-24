"""
Fetch OSM tags for stations that have just entered the registry.

A station is registered the moment a user picks it, from what the autocomplete already knew:
a name, a country and a position. Its OSM tags — `wikidata`, `uic_ref`, `int_name`,
`alt_name` and every `name:*` — are deliberately *not* fetched at that moment, because a trip
save must never wait on a third-party API. The row is created with `enriched_at NULL`, and
this script is what fills it in.

Until it runs, a station works but is thin: it resolves and displays, but it is not findable
by its other-language names, its duplicates cannot be collapsed exactly, and it has no stable
identity to merge on.

A background thread does this automatically: src/station_osm.start_station_enricher, started
at boot alongside the email listener. Trainlog runs no scheduler and nothing is cronned, so
this script is not the mechanism — it is for draining by hand, after a bulk import or to
catch up without waiting for the next pass:

    python3 scripts/enrich_stations.py --max-batches 20

Bounded on purpose. One batch is 60 stations in one Overpass request (measured 2.19s), and
--max-batches caps a single run so one pass cannot become an hour of Overpass traffic;
whatever is left stays queued for the next one. A batch that fails is left queued rather
than retried in a tight loop.

This project has already been banned from one free geocoder for excessive use, so the
batching, the pause between batches and the identifying User-Agent in src/station_osm.py are
requirements, not tuning.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts._env import load_env  # noqa: E402

# Reads .env and picks a database host that resolves from wherever this is run,
# so the script works both on the server and from a local shell.
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

    # Non-zero on failure, so a caller that checks exit codes notices.
    return 1 if result["failed_batches"] else 0


if __name__ == "__main__":
    sys.exit(main())
