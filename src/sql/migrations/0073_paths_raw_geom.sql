-- Preserve the original raw GPS trace alongside the (possibly routed/cleaned)
-- path geometry, so a GPX-derived trip can later be re-corrected against the
-- exact source track instead of only the cleaned line. Previously the raw
-- trace only lived in the `gpx` staging table, deleted as soon as the trip
-- was saved. NULL for every trip not built from a GPX upload.
ALTER TABLE paths ADD COLUMN raw_geom geometry(Geometry, 4326);
