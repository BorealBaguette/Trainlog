-- Carry per-point elevation and time from imported GPX/KML tracks through the
-- staging table, so a raw (unrouted) import can persist them onto paths.altitude
-- and paths.timestamps — parallel to the stored geometry, same as a flight track.
ALTER TABLE gpx ADD COLUMN altitude jsonb;
ALTER TABLE gpx ADD COLUMN timestamps jsonb;
