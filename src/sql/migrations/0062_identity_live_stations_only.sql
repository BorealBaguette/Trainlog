-- Identity uniqueness applies among live stations only.
--
-- 0058 indexed wikidata and uic_ref across every row, superseded or not. merge_stations()
-- moves a source's OSM objects and aliases to the target but leaves its identity anchors in
-- place, so a merged-away husk goes on squatting a global identity — and blocks the very
-- station it was merged into from ever acquiring it.
--
-- Observed: station 146 was merged into 145 and kept wikidata Q801124. Enrichment of 145 then
-- read Q801124 from OSM, tried to store it, and hit stations_wikidata_key against the dead
-- row. The batch aborted, nothing was stamped, and the enricher retried the same collision
-- every ten minutes indefinitely — the queue cannot drain past a row that can never succeed.
--
-- Excluding superseded rows is the right shape rather than clearing their columns on merge:
-- a husk exists only so reads can follow superseded_by, and what it used to be identified by
-- is worth keeping. It simply must not compete for identity with stations that are still real.
--
-- osm_id is included for the same reason. merge_stations() moves station_osm_objects across
-- but leaves stations.osm_type/osm_id on the source, so a husk squats its object too.

DROP INDEX IF EXISTS stations_wikidata_key;
DROP INDEX IF EXISTS stations_uic_ref_key;
DROP INDEX IF EXISTS stations_osm_key;

CREATE UNIQUE INDEX stations_wikidata_key ON stations (station_type, wikidata)
    WHERE wikidata IS NOT NULL AND superseded_by IS NULL;
CREATE UNIQUE INDEX stations_uic_ref_key ON stations (station_type, uic_ref)
    WHERE uic_ref IS NOT NULL AND superseded_by IS NULL;
CREATE UNIQUE INDEX stations_osm_key ON stations (osm_type, osm_id)
    WHERE osm_id IS NOT NULL AND superseded_by IS NULL;

-- Hand the anchors to the survivors that have none. Without this, 145 stays anonymous until
-- its next enrichment pass, and a survivor whose own OSM object carries no wikidata would
-- never inherit one at all. Only where the survivor is empty: where it already holds a
-- different QID an admin merged two things OSM considers distinct, and that is their call.
UPDATE stations t
SET wikidata = s.wikidata
FROM stations s
WHERE s.superseded_by = t.station_id
  AND s.wikidata IS NOT NULL
  AND t.wikidata IS NULL
  AND t.station_type = s.station_type;

UPDATE stations t
SET uic_ref = s.uic_ref
FROM stations s
WHERE s.superseded_by = t.station_id
  AND s.uic_ref IS NOT NULL
  AND t.uic_ref IS NULL
  AND t.station_type = s.station_type;

ANALYZE stations;
