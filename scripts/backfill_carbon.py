"""
Backfill carbon footprint for all existing trips
"""

import json
import logging

from src.carbon import calculate_carbon_footprint_for_trip
from src.pg import pg_session
from src.utils import mainConn, managed_cursor, pathConn

logger = logging.getLogger(__name__)


def backfill_carbon_for_all_trips():
    """
    Calculate and update carbon footprint for all trips that don't have it set
    """
    with pg_session() as pg:
        # Get all trips without carbon values (or all trips if you want to recalculate)
        result = pg.execute(
            "SELECT trip_id FROM trips WHERE carbon IS NULL ORDER BY trip_id"
        ).fetchall()

        trip_ids = [row["trip_id"] for row in result]
        total = len(trip_ids)

        logger.info(f"Found {total} trips to backfill")

        for idx, trip_id in enumerate(trip_ids, 1):
            # Fetch trip data from SQLite
            with managed_cursor(mainConn) as cursor:
                cursor.execute("SELECT * FROM trip WHERE uid = ?", (trip_id,))
                row = cursor.fetchone()

            if not row:
                logger.warning(f"Trip {trip_id} not found in SQLite")
                continue

            # Convert sqlite3.Row to dict explicitly
            sqlite_trip = {key: row[key] for key in row.keys()}

            # Fetch path data
            with managed_cursor(pathConn) as cursor:
                cursor.execute("SELECT path FROM paths WHERE trip_id = ?", (trip_id,))
                path_row = cursor.fetchone()

            if not path_row:
                logger.warning(f"Path not found for trip {trip_id}")
                continue

            path_data = (
                json.loads(path_row["path"])
                if isinstance(path_row["path"], str)
                else path_row["path"]
            )

            # Convert path data to the format Path expects
            # Path data might be [[lat, lng], [lat, lng]] or [{"lat": x, "lng": y}, ...]
            if path_data and isinstance(path_data[0], list):
                # Convert [[lat, lng], ...] to [{"lat": lat, "lng": lng}, ...]
                path_data_formatted = [
                    {"lat": coord[0], "lng": coord[1]} for coord in path_data
                ]
            else:
                path_data_formatted = path_data

            # Create Path object for any operations that might need it
            # path = Path(path=path_data_formatted, trip_id=trip_id)

            # Create a dict with trip data for carbon calculation
            trip_data = {
                "trip_id": trip_id,
                "type": sqlite_trip["type"],
                "trip_length": sqlite_trip["trip_length"],
                "origin_station": sqlite_trip["origin_station"],
                "destination_station": sqlite_trip["destination_station"],
                "operator": sqlite_trip["operator"],
                "countries": sqlite_trip["countries"],
                "start_datetime": sqlite_trip.get("start_datetime"),
                "end_datetime": sqlite_trip.get("end_datetime"),
                "estimated_trip_duration": sqlite_trip.get("estimated_trip_duration"),
                "manual_trip_duration": sqlite_trip.get("manual_trip_duration"),
                "line_name": sqlite_trip.get("line_name"),
                "material_type": sqlite_trip.get("material_type"),
                "seat": sqlite_trip.get("seat"),
                "reg": sqlite_trip.get("reg"),
                "waypoints": sqlite_trip.get("waypoints"),
                "notes": sqlite_trip.get("notes"),
            }

            # Calculate carbon - pass the formatted path data, not the Path object
            carbon = calculate_carbon_footprint_for_trip(trip_data, path_data_formatted)

            # Update in PostgreSQL
            pg.execute(
                "UPDATE trips SET carbon = :carbon WHERE trip_id = :trip_id",
                {"carbon": carbon, "trip_id": trip_id},
            )

            # Commit every 100 trips to avoid losing too much progress
            if idx % 100 == 0:
                pg.commit()
                logger.info(f"Progress: {idx}/{total} trips processed and committed")

        # Final commit for any remaining trips
        pg.commit()
        logger.info(f"Backfill complete: {total} trips processed")


def main():
    logging.basicConfig(level=logging.DEBUG)
    backfill_carbon_for_all_trips()


if __name__ == "__main__":
    main()
