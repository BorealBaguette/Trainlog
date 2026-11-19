-- Where a routed path runs in a tunnel or over a bridge: the rail router's road_environment
-- detail, as [[start point, end point, "tunnel" | "bridge" | "ferry"], ...] on the points of
-- geom, kept with the track heights (in altitude) for the 3D trip view. NULL for paths not
-- routed by the rail router, or saved before it was kept.
--
-- The indices only mean something for the geometry they came with, and paths are written
-- from many places (edits, splits, the freehand converter, ...), so a trigger clears the
-- column whenever geom changes without the same statement setting it again.
ALTER TABLE paths ADD COLUMN track_env jsonb;

CREATE OR REPLACE FUNCTION paths_clear_stale_track_env() RETURNS trigger AS $$
BEGIN
    IF NEW.geom IS DISTINCT FROM OLD.geom AND NEW.track_env IS NOT DISTINCT FROM OLD.track_env THEN
        NEW.track_env := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER paths_track_env_stale
    BEFORE UPDATE OF geom ON paths
    FOR EACH ROW EXECUTE FUNCTION paths_clear_stale_track_env();
