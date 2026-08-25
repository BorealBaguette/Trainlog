-- One OSM object may anchor a station in more than one mode.
--
-- 0058 keyed these on (osm_type, osm_id) alone, so an object belonged to one station anywhere
-- — which contradicts the pool rule the registry is built on, since at an interchange OSM
-- often offers one node as the best anchor for both the metro stop and the station above it.
-- The metro label "London - Seven Sisters" therefore resolved to nothing and could not be
-- fixed from the admin panel, because every attempt found the train station and stopped.
--
-- Now scoped by pool, matching the wikidata and uic_ref indexes.

ALTER TABLE station_osm_objects DROP CONSTRAINT station_osm_objects_pkey;
ALTER TABLE station_osm_objects ADD PRIMARY KEY (osm_type, osm_id, station_id);

DROP INDEX IF EXISTS stations_osm_key;
CREATE UNIQUE INDEX stations_osm_key ON stations (station_type, osm_type, osm_id)
    WHERE osm_id IS NOT NULL AND superseded_by IS NULL;
