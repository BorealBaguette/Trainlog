"""
One-shot migration: copy all data from the SQLite databases (main.db / path.db)
into PostgreSQL.

Run this ON THE SERVER (inside the app container, where POSTGRES_HOST resolves and
the SQLite files are present) after the PG migrations have been applied:

    docker compose exec trainlog python -m scripts.sync_to_pg

It is idempotent: each table is cleared and re-copied, so it can be re-run safely.
Covers: trips, operators (+logos), aux tables (percents/exchanges/daily_active_users/
fr24_usage/ai_usage), trip-related (tickets/tags/tags_associations/gpx/ship_pictures),
reference (airports/train_stations/manual_stations/here_api_operators) and paths (PostGIS).
"""

import logging

from src.db_sync import sync_db_from_sqlite


def main():
    logging.basicConfig(level=logging.INFO)
    sync_db_from_sqlite()


if __name__ == "__main__":
    main()
