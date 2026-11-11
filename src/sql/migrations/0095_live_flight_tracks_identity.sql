-- Live flight tracks are now tied to the flight they were resolved for (flight number, or
-- registration when there is none, and scheduled departure) instead of
-- trips.last_modified, so editing a note mid-flight no longer throws away the resolved
-- aircraft. Old values never match the new keys, so every existing row is resolved again
-- once.
-- Only where it has not been done yet: a database that ran this file under its earlier name
-- (0092_live_flight_tracks_identity.sql, before it was renumbered) has flight_key already.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'live_flight_tracks' AND column_name = 'trip_seen_modified') THEN
        ALTER TABLE live_flight_tracks RENAME COLUMN trip_seen_modified TO flight_key;
    END IF;
END $$;

-- New terminal status 'ended': FR24 lost the aircraft for good away from its destination
-- (or never saw it land). The row is no longer polled or drawn, and the trip keeps the
-- route it was logged with.
