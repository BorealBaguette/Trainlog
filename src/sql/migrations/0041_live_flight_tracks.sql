-- Live tracking of in-progress flights.
--
-- A flight logged before it happens has no real route, so it is stored as a 2-point
-- LINESTRING and drawn as a geodesic. While it is actually in the air we can fetch the
-- flown-so-far track from FR24 and draw that instead, with only the remainder estimated.
--
-- This is deliberately NOT written into `paths`: a partial track there would (a) leave the
-- user's saved trip truncated until landing and (b) be picked up by the map's IndexedDB
-- cache, freezing the route at whatever point a viewer first loaded it. The live geometry
-- therefore lives here and is overlaid at render time; on landing it is promoted into
-- `paths` once, complete, and the row here is deleted.
--
-- Columns mirror `paths` (see 0032_paths_3d_track.sql) so that promotion is a plain copy
-- and the existing 3D altitude rendering works unchanged.
CREATE TABLE IF NOT EXISTS live_flight_tracks (
    trip_id     INTEGER PRIMARY KEY REFERENCES trips(trip_id) ON DELETE CASCADE,
    -- FR24's per-flight-INSTANCE id. Unknown until the flight exists, so it is resolved
    -- on first poll from trips.line_name (flight number) and cached here. Meaningless
    -- after landing, which is why it does not belong on `trips`.
    fr24_id     TEXT,
    geom        geometry(Geometry, 4326),
    altitude    jsonb,
    timestamps  jsonb,
    last_polled timestamptz NOT NULL DEFAULT now(),
    -- Observability only, not a limit: cost is bounded by the polling window in
    -- src/api/live_tracks.py, so that a long-haul leg is never cut off mid-flight.
    poll_count  INTEGER NOT NULL DEFAULT 0,
    -- pending    : row created, flight not resolved yet
    -- live       : resolved, track being refreshed
    -- landed     : final track promoted into `paths` (row about to be deleted)
    -- unresolved : FR24 has no such flight (wrong number, private charter, non-FR24
    --              airline, or no flight number at all). Stops us retrying every 5
    --              minutes for the whole duration of the trip.
    status      TEXT NOT NULL DEFAULT 'pending',
    -- Mirrors trips.last_modified at resolution time. If the user corrects the flight
    -- number mid-flight the values diverge and the row is re-resolved.
    trip_seen_modified TEXT
);

CREATE INDEX IF NOT EXISTS live_flight_tracks_polled_idx
    ON live_flight_tracks (last_polled);
