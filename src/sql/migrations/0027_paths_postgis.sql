-- Trip route geometry migrated from SQLite path.db (paths.path was a JSON
-- [[lat,lng],...] array) to PostGIS. Generic Geometry so 1-point / 2-point
-- (geodesic flight) / multi-point routes all fit.
CREATE TABLE paths (
    trip_id INTEGER PRIMARY KEY,
    geom geometry(Geometry, 4326) NOT NULL
);
CREATE INDEX paths_geom_gix ON paths USING GIST (geom);
