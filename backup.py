#!/usr/bin/env python3
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

# ─── Configuration ──────────────────────────────────────────────────────────────

# Folder where live DBs live, and where backups go
SRC_DIR = Path("databases")
BASE_BACKUP_DIR = Path("backup")

# SQLite DBs that remain after the PostgreSQL migration (auth + error log).
# All trip/path/reference data now lives in PostgreSQL (backed up via pg_dump).
SIMPLE_DBS = ["auth.db"]

# Max parameters per SQLite query (keep below 999)
CHUNK_SIZE = 900

# History of recent pg_dump runs (duration + dump size), used to estimate how
# long the next dump will take and to render a live progress bar.
PG_HISTORY_FILE = BASE_BACKUP_DIR / "pg_dump_history.json"
PG_HISTORY_KEEP = 10

# ─── Progress Bar Class ─────────────────────────────────────────────────────────


class ProgressBar:
    def __init__(self, total: int, description: str = "", width: int = 40):
        self.total = total
        self.current = 0
        self.description = description
        self.width = width
        self.start_time = time.time()
        self.last_update = 0

    def update(self, amount: int = 1):
        """Update progress by amount and display if enough time has passed."""
        self.current = min(self.current + amount, self.total)

        # Update display at most every 0.1 seconds to avoid flickering
        current_time = time.time()
        if current_time - self.last_update >= 0.1 or self.current == self.total:
            self._display()
            self.last_update = current_time

    def _display(self):
        """Display the current progress bar."""
        if self.total == 0:
            percentage = 100
            filled_length = self.width
        else:
            percentage = (self.current / self.total) * 100
            filled_length = int(self.width * self.current // self.total)

        bar = "█" * filled_length + "▒" * (self.width - filled_length)

        # Calculate time estimates
        elapsed = time.time() - self.start_time
        if self.current > 0 and self.current < self.total:
            rate = self.current / elapsed
            remaining = (self.total - self.current) / rate
            time_str = f" | ETA: {self._format_time(remaining)}"
        elif self.current == self.total:
            time_str = f" | Done in {self._format_time(elapsed)}"
        else:
            time_str = " | Calculating..."

        # Build the progress line
        progress_line = f"\r{self.description} |{bar}| {percentage:5.1f}% ({self.current}/{self.total}){time_str}"

        # Print and ensure we don't leave trailing characters
        print(progress_line.ljust(80), end="", flush=True)

        if self.current == self.total:
            print()  # New line when complete

    def _format_time(self, seconds: float) -> str:
        """Format seconds into human-readable time."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"


# ─── Helper Functions ────────────────────────────────────────────────────────────


def now_iso_date():
    """Return today's date in YYYY-MM-DD for Europe/Oslo."""
    tz = ZoneInfo("Europe/Oslo")
    return datetime.now(tz).strftime("%Y-%m-%d")


def connect_readonly(path: Path):
    """Open a read-only, immutable SQLite URI connection."""
    uri = f"file:{path}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def connect_writable(path: Path):
    """Open or create a writable SQLite file."""
    return sqlite3.connect(path)


def get_table_row_count(conn: sqlite3.Connection, table: str) -> int:
    """Get the number of rows in a table."""
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


def get_all_tables_row_count(conn: sqlite3.Connection) -> int:
    """Get total row count across all user tables."""
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    total = 0
    for (table,) in cur.fetchall():
        total += get_table_row_count(conn, table)
    return total


def copy_schema_and_data(
    src_conn, dst_conn, table_filter=None, progress_callback: Optional[Callable] = None
):
    """
    Copy all tables/indexes/triggers/views from src to dst.
    Skips sqlite_sequence and any tables rejected by table_filter(name)->bool.
    """
    src = src_conn.cursor()
    dst = dst_conn.cursor()

    # Grab all user-and-meta objects (tables, indexes, triggers, views)
    src.execute("""
        SELECT type, name, sql 
          FROM sqlite_master 
         WHERE name NOT LIKE 'sqlite_%' 
           AND sql IS NOT NULL
         ORDER BY type='table' DESC, type='index', type;
    """)
    schema_objects = src.fetchall()

    # Create schema objects
    for obj_type, name, sql in schema_objects:
        if table_filter and not table_filter(name, obj_type):
            continue
        dst.execute(sql)
    dst_conn.commit()

    # Copy table data with progress tracking
    src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
    )
    tables = [row[0] for row in src.fetchall()]

    for tbl in tables:
        if table_filter and not table_filter(tbl, "table"):
            continue

        # Get all rows for this table
        rows = src.execute(f"SELECT * FROM {tbl}").fetchall()
        if not rows:
            continue

        # Insert rows and update progress
        placeholders = ", ".join(["?"] * len(rows[0]))
        dst.executemany(f"INSERT INTO {tbl} VALUES ({placeholders})", rows)

        if progress_callback:
            progress_callback(len(rows))

    dst_conn.commit()


# ─── Backup Routines ────────────────────────────────────────────────────────────


def backup_simple(db_name: str, dst_folder: Path):
    """Copy schema + all data from a simple DB (no cross-filtering)."""
    src = SRC_DIR / db_name
    dst = dst_folder / db_name

    print(f"Analyzing {db_name}...")

    # Get total row count for progress tracking
    with connect_readonly(src) as src_conn:
        total_rows = get_all_tables_row_count(src_conn)

    if total_rows == 0:
        print(f"✅ {db_name} is empty, skipping.")
        return

    # Create progress bar
    progress = ProgressBar(total_rows, f"Backing up {db_name}")

    def update_progress(rows_processed):
        progress.update(rows_processed)

    # Perform backup with progress tracking
    with connect_readonly(src) as src_conn, connect_writable(dst) as dst_conn:
        copy_schema_and_data(src_conn, dst_conn, progress_callback=update_progress)


# ─── PostgreSQL backup ──────────────────────────────────────────────────────────


def _format_size(num_bytes: float) -> str:
    """Format a byte count as MB/GB."""
    mb = num_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"


def _format_duration(seconds: float) -> str:
    """Format seconds into a short human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"


def load_pg_history() -> list:
    """Load the recorded pg_dump runs (newest last). Tolerates a missing/corrupt file."""
    try:
        with open(PG_HISTORY_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def save_pg_run(duration_sec: float, size_bytes: int):
    """Append a completed run and keep only the most recent PG_HISTORY_KEEP entries."""
    history = load_pg_history()
    history.append(
        {
            "timestamp": datetime.now(ZoneInfo("Europe/Oslo")).isoformat(timespec="seconds"),
            "duration_sec": round(duration_sec, 2),
            "size_bytes": int(size_bytes),
        }
    )
    history = history[-PG_HISTORY_KEEP:]
    PG_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PG_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def estimate_pg(history: list):
    """Estimate (final_size_bytes, sec_per_byte) for the next dump from past runs.

    Final size is taken from the most recent run (best predictor of a growing DB);
    sec_per_byte is a rough baseline averaged over all recorded runs. Returns
    (None, None) when there's no usable history yet.
    """
    runs = [r for r in history if r.get("size_bytes")]
    if not runs:
        return None, None

    est_size = runs[-1]["size_bytes"]
    total_dur = sum(r["duration_sec"] for r in runs)
    total_size = sum(r["size_bytes"] for r in runs)
    sec_per_byte = total_dur / total_size if total_size else None
    return est_size, sec_per_byte


def _display_pg_progress(current, est_size, sec_per_byte, start, width=40, final=False):
    """Render the live pg_dump progress line based on the growing dump file size."""
    elapsed = time.time() - start

    if est_size:
        frac = 1.0 if final else min(current / est_size, 1.0)
        filled = int(width * frac)
        bar = "█" * filled + "▒" * (width - filled)
        pct = 100.0 if final else min(frac * 100, 99.9)

        if final:
            tail = f" | Done in {_format_duration(elapsed)}"
        elif sec_per_byte and current < est_size:
            remaining = (est_size - current) * sec_per_byte
            tail = f" | ETA: {_format_duration(remaining)}"
        else:
            tail = " | finishing..."

        line = (
            f"\rBacking up PostgreSQL |{bar}| {pct:5.1f}% "
            f"({_format_size(current)} / ~{_format_size(est_size)}){tail}"
        )
    else:
        # First run (no history): we can't estimate completion, just show progress.
        line = (
            f"\rBacking up PostgreSQL | {_format_size(current)} written "
            f"| {_format_duration(elapsed)}"
        )

    print(line.ljust(90), end="", flush=True)
    if final:
        print()


def backup_postgres(dst_folder: Path):
    """Dump the PostgreSQL database (trips, paths, tickets, tags, reference data,
    finance, meta, ...) to a compressed custom-format file.

    Runs pg_dump inside the postgis container (via `docker exec`, targeting the
    container named by POSTGRES_HOST) so the client version always matches the
    server and it works regardless of the current working directory.
    Restore with: pg_restore --clean --if-exists -U <user> -d <db> < trainlog_pg.dump

    The duration and resulting dump size of each run are recorded in
    PG_HISTORY_FILE, so the next run can show a live progress bar / ETA derived
    from a rough sec/MB baseline and the dump file's current size.
    """
    db = os.environ.get("POSTGRES_DB", "postgres")
    user = os.environ.get("POSTGRES_USER", "trainlog")
    container = os.environ.get("POSTGRES_HOST", "trainlog_db")

    out = dst_folder / "trainlog_pg.dump"
    print(f"Backing up PostgreSQL database '{db}' -> {out}")

    est_size, sec_per_byte = estimate_pg(load_pg_history())
    if est_size:
        eta = sec_per_byte * est_size if sec_per_byte else None
        eta_str = f", ~{_format_duration(eta)}" if eta else ""
        print(
            f"   Estimated from history: ~{_format_size(est_size)}{eta_str} "
            f"(based on last run / sec-per-MB baseline)"
        )
    else:
        print("   No history yet — this run will calibrate future estimates.")

    start = time.time()
    with open(out, "wb") as f:
        proc = subprocess.Popen(
            [
                "docker", "exec", "-i", container,
                "pg_dump",
                "-Fc",  # compressed custom format (restore with pg_restore)
                "-U", user,
                "-d", db,
                "-n", "public",
                "-n", "finance",
                "-n", "meta",
            ],
            stdout=f,
        )

        # Poll the growing dump file to drive the progress bar.
        while proc.poll() is None:
            try:
                current = out.stat().st_size
            except FileNotFoundError:
                current = 0
            _display_pg_progress(current, est_size, sec_per_byte, start)
            time.sleep(0.5)

        rc = proc.wait()

    if rc != 0:
        raise subprocess.CalledProcessError(rc, "pg_dump")

    duration = time.time() - start
    final_size = out.stat().st_size
    _display_pg_progress(final_size, est_size or final_size, sec_per_byte, start, final=True)

    save_pg_run(duration, final_size)
    print(
        f"✅ PostgreSQL backup complete — {_format_size(final_size)} "
        f"in {_format_duration(duration)}"
    )


# ─── Main Script ────────────────────────────────────────────────────────────────


def main():
    print("🔄 Starting database backup...\n")

    # 1) Prepare destination folder
    date_str = now_iso_date()
    dst = BASE_BACKUP_DIR / date_str
    dst.mkdir(parents=True, exist_ok=True)
    print(f"📁 Backing up to folder: {dst}\n")

    # 2) Remaining SQLite DBs (auth + error log)
    for db in SIMPLE_DBS:
        backup_simple(db, dst)
        print()  # Add spacing between databases

    # 3) PostgreSQL (everything else)
    backup_postgres(dst)

    print("\n✅ Backup complete!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  Backup interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Backup failed: {e}", file=sys.stderr)
        sys.exit(1)
