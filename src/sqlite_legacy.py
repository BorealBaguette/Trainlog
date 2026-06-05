"""Legacy SQLite connections for the `main` and `path` databases.

These are used ONLY by the one-time SQLite->PostgreSQL migration tooling
(src/db_sync.py and the compare_trip verification helper). The application
runtime no longer reads or writes SQLite for trip/path/reference data — it is
fully on PostgreSQL.

Connections are created only if the SQLite files still exist, so a fresh
PostgreSQL-only deployment does not accidentally create empty .db files.
"""

import os
import sqlite3

from src.consts import DbNames


def _connect(path):
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


mainConn = _connect(DbNames.MAIN_DB.value)
pathConn = _connect(DbNames.PATH_DB.value)
