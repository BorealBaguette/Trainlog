"""
Backfill carbon footprint for all existing trips
"""
import json
import logging

from psycopg2.extras import execute_values
from sqlalchemy import text

from src.pg import pg_session
from src.carbon import calculate_carbon_footprint_for_trip

logger = logging.getLogger(__name__)


def derive_power_type(trip_type, countries):
    """Best-effort recovery of a trip's power type from its stored `countries`
    breakdown, for rows migrated before power_type was a column.

    Only the elec/nonelec breakdown carries this information; the plain
    {cc: meters} format does not. Returns 'electric'/'thermic'/'auto', or None
    when undeterminable (caller leaves the column NULL -> carbon falls back to
    the type default).
    """
    if not countries:
        return None
    if isinstance(countries, str):
        try:
            countries = json.loads(countries)
        except Exception:
            return None
    if not isinstance(countries, dict):
        return None

    total_elec = total_nonelec = 0.0
    saw_split = False
    for value in countries.values():
        if isinstance(value, dict) and ("elec" in value or "nonelec" in value):
            saw_split = True
            total_elec += value.get("elec", 0) or 0
            total_nonelec += value.get("nonelec", 0) or 0

    if not saw_split:
        return None
    if total_nonelec == 0 and total_elec > 0:
        return "electric"
    if total_elec == 0 and total_nonelec > 0:
        return "thermic"
    if total_elec > 0 and total_nonelec > 0:
        # Partial electrification only legitimately happens for rail (OSM-derived);
        # keep the stored split by labelling it 'auto' rather than forcing a side.
        return "auto" if trip_type in ("train", "rail") else None
    return None


# Only these columns feed the carbon calculation. The route geometry is NOT
# needed: calculate_carbon_footprint_for_trip derives distance from trip_length,
# and the only `path` use is len(path) passed to the air branch, which ignores it.
_SELECT_COLS = (
    "trip_id, trip_type AS type, trip_length, countries, start_datetime,"
    " material_type, power_type, co2_override"
)


def backfill_carbon_for_all_trips(batch_size=5000):
    """
    Recalculate carbon for every trip and, where it was never stored, recover the
    power_type from the trip's countries breakdown. Recomputes all rows (not just
    NULL carbon) because the earlier backfill produced wrong figures (it ignored
    power_type / co2_override).

    Streams trips in keyset batches and writes each batch with a single
    UPDATE ... FROM (VALUES ...), so there is no per-trip query and no path fetch.
    """
    with pg_session() as pg:
        total = pg.execute(text("SELECT count(*) FROM trips")).scalar()
        logger.info(f"Backfilling carbon for {total} trips (batch size {batch_size})")

        last_id = 0
        processed = 0
        while True:
            rows = pg.execute(
                text(
                    f"SELECT {_SELECT_COLS} FROM trips"
                    " WHERE trip_id > :last ORDER BY trip_id LIMIT :lim"
                ),
                {"last": last_id, "lim": batch_size},
            ).fetchall()
            if not rows:
                break

            updates = []
            for row in rows:
                r = row._mapping
                # Recover power_type from the countries breakdown when it was never
                # stored (pre-column rows); keep an explicit value if already set.
                power_type = r["power_type"] or derive_power_type(
                    r["type"], r["countries"]
                )
                trip_data = {
                    "type": r["type"],
                    "trip_length": r["trip_length"],
                    "countries": r["countries"],
                    "start_datetime": r["start_datetime"],
                    "material_type": r["material_type"],
                    "power_type": power_type,
                    "co2_override": r["co2_override"],
                }
                carbon = calculate_carbon_footprint_for_trip(trip_data, [])
                updates.append((r["trip_id"], carbon, power_type))

            last_id = rows[-1]._mapping["trip_id"]

            # One round-trip per batch: join the new values back onto trips by id.
            raw = pg.connection().connection.cursor()
            execute_values(
                raw,
                "UPDATE trips SET carbon = data.carbon, power_type = data.power_type"
                " FROM (VALUES %s) AS data(trip_id, carbon, power_type)"
                " WHERE trips.trip_id = data.trip_id",
                updates,
                template="(%s::int, %s::real, %s::text)",
                page_size=batch_size,
            )
            pg.commit()

            processed += len(rows)
            logger.info(f"Progress: {processed}/{total} trips processed and committed")

        logger.info(f"Backfill complete: {processed} trips processed")


def main():
    logging.basicConfig(level=logging.DEBUG)
    backfill_carbon_for_all_trips()


if __name__ == "__main__":
    main()