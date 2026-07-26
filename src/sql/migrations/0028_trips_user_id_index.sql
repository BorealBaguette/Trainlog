-- Index trips by user_id. Every /u/<user>/trips request filters the trips table
-- by user_id (twice: once for the DataTables count, once for the page). Without
-- this index Postgres sequentially scans the entire trips table on each request,
-- which scales with total table size rather than the user's trip count.
CREATE INDEX IF NOT EXISTS idx_trips_user_id ON trips (user_id);
