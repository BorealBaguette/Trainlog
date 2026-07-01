-- Snapshot of a trip's route taken the first time /freehandify simplifies it, so the
-- change can be undone and re-run at a different tolerance from the full-detail
-- original (re-running always simplifies from this snapshot, never the already-thinned
-- geometry). One row per trip; dropped on revert. Cascades if the trip is deleted.
-- This is a short-lived undo buffer, not an archive: rows are purged after a TTL
-- (BACKUP_TTL_DAYS in src/trips/freehand_transform.py, default 7 days) opportunistically
-- on every freehandify call and by /admin/freehandify/cleanup. The `created` column
-- drives that expiry.
CREATE TABLE IF NOT EXISTS freehand_backup (
    trip_id      INTEGER PRIMARY KEY REFERENCES trips(trip_id) ON DELETE CASCADE,
    geom         geometry(Geometry, 4326) NOT NULL,
    waypoints    TEXT,
    route_source TEXT,
    created      TIMESTAMPTZ DEFAULT NOW()
);
