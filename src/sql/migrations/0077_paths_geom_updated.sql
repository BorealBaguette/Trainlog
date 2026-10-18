-- Version stamp for a path's geometry, so the trips map (new_map.html) can keep
-- paths cached on the device and re-download only the ones that changed.
-- trips.last_modified can't serve: paths are written from many places
-- (create/update/split/duplicate/freehand/live tracks…) and not all of them
-- touch the trip row. A trigger keeps this column honest whatever the writer.
-- The constant default makes the ADD COLUMN metadata-only (no table rewrite);
-- every existing row gets the migration time, which is fine for a version.
ALTER TABLE paths ADD COLUMN geom_updated timestamp NOT NULL DEFAULT now();

CREATE OR REPLACE FUNCTION paths_touch_geom_updated() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' OR NEW.geom IS DISTINCT FROM OLD.geom THEN
        NEW.geom_updated := clock_timestamp();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER paths_geom_updated
    BEFORE INSERT OR UPDATE OF geom ON paths
    FOR EACH ROW EXECUTE FUNCTION paths_touch_geom_updated();
