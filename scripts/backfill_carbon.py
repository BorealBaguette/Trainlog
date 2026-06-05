"""
Backfill carbon footprint for all existing trips
"""
import logging
from src.pg import pg_session
from src.carbon import calculate_carbon_footprint_for_trip
from src.paths import fetch_path

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
        
        trip_ids = [row['trip_id'] for row in result]
        total = len(trip_ids)
        
        logger.info(f"Found {total} trips to backfill")
        
        for idx, trip_id in enumerate(trip_ids, 1):
            # Fetch trip data from PostgreSQL
            row = pg.execute(
                "SELECT trip_type AS type, trip_length, origin_station, destination_station,"
                " operator, countries, start_datetime, end_datetime, estimated_trip_duration,"
                " manual_trip_duration, line_name, material_type, seat, reg, waypoints, notes"
                " FROM trips WHERE trip_id = :tid",
                {"tid": trip_id},
            ).fetchone()

            if not row:
                logger.warning(f"Trip {trip_id} not found")
                continue

            sqlite_trip = dict(row._mapping)

            # Fetch path geometry as [[lat, lng], ...]
            path_data = fetch_path(pg, trip_id)
            if not path_data:
                logger.warning(f"Path not found for trip {trip_id}")
                continue

            path_data_formatted = [{"lat": coord[0], "lng": coord[1]} for coord in path_data]

            # Create a dict with trip data for carbon calculation
            trip_data = {
                'trip_id': trip_id,
                'type': sqlite_trip['type'],
                'trip_length': sqlite_trip['trip_length'],
                'origin_station': sqlite_trip['origin_station'],
                'destination_station': sqlite_trip['destination_station'],
                'operator': sqlite_trip['operator'],
                'countries': sqlite_trip['countries'],
                'start_datetime': sqlite_trip.get('start_datetime'),
                'end_datetime': sqlite_trip.get('end_datetime'),
                'estimated_trip_duration': sqlite_trip.get('estimated_trip_duration'),
                'manual_trip_duration': sqlite_trip.get('manual_trip_duration'),
                'line_name': sqlite_trip.get('line_name'),
                'material_type': sqlite_trip.get('material_type'),
                'seat': sqlite_trip.get('seat'),
                'reg': sqlite_trip.get('reg'),
                'waypoints': sqlite_trip.get('waypoints'),
                'notes': sqlite_trip.get('notes'),
            }
            
            # Calculate carbon - pass the formatted path data, not the Path object
            carbon = calculate_carbon_footprint_for_trip(trip_data, path_data_formatted)
            
            # Update in PostgreSQL
            pg.execute(
                "UPDATE trips SET carbon = :carbon WHERE trip_id = :trip_id",
                {"carbon": carbon, "trip_id": trip_id}
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