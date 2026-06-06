-- Trip route geometry migrated from SQLite path.db (paths.path was a JSON
-- [[lat,lng],...] array) to PostGIS. Generic Geometry so 1-point / 2-point
-- (geodesic flight) / multi-point routes all fit.
-- The geometry type comes from PostGIS; the postgis/postgis image auto-creates
-- the extension in its default DB, but a plain-postgres or pre-existing DB won't
-- have it, so create it here (mirrors 0013 pg_trgm / 0024 unaccent).
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE paths (
    trip_id INTEGER PRIMARY KEY,
    geom geometry(Geometry, 4326) NOT NULL
);
CREATE INDEX paths_geom_gix ON paths USING GIST (geom);
