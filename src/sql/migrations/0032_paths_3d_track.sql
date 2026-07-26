-- 3D flight track: altitude (Z) and timestamp (T) per geom vertex, captured from
-- the FlightRadar24 import. Both are JSON arrays parallel to the path's geom
-- vertices (altitude in metres, timestamps in epoch seconds). NULL for every
-- non-flight and for flights imported before this feature (no backfill).
ALTER TABLE paths ADD COLUMN altitude jsonb;
ALTER TABLE paths ADD COLUMN timestamps jsonb;
