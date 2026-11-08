-- The cities the rail network graph groups train stations into: GHSL urban centres
-- (scripts/build_urban_areas.py), seeded from base_data/urban_areas.csv by setup_db().
CREATE TABLE urban_areas (
    id         integer PRIMARY KEY,
    name       text,                 -- a few centres have none: the graph names them after a station
    country    text,
    population integer NOT NULL,
    geom       geometry(MultiPolygon, 4326) NOT NULL
);

CREATE INDEX urban_areas_geom_idx ON urban_areas USING gist (geom);
