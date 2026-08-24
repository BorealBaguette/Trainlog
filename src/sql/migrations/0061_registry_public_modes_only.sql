-- Restrict the station registry to public transport, and delete what it collected outside it.
--
-- A deletion and not just an exclusion, because the rows already collected are the problem:
-- the unresolved queue displays `sample_label` verbatim ordered by occurrence count, which
-- would put the most-visited private addresses at the top of a page an admin opens.
--
-- Nothing is lost that cannot be rebuilt — `station_labels` is a derived cache and
-- `trips.origin_station` is untouched, so rebuild_labels() re-derives a mode if it is ever
-- tracked again. Measured before this ran: 27,457 label rows and 70,785 trip endpoints across
-- these modes, and zero registered stations, which is what places with no canonical name look
-- like.
--
-- See REGISTRY_EXCLUDED_TYPES in src/stations.py for the full rule and its reasons.


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. The rule
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION station_type_tracked(trip_type text) RETURNS boolean AS $$
    SELECT station_type_bucket($1) NOT IN (
        'air',
        -- Personal modes: the endpoints are wherever the person was.
        'car', 'walk', 'cycle', 'scooter',
        -- Personal destinations: somewhere they chose to go.
        'accommodation', 'restaurant', 'poi',
        -- The catch-all bucket. Its contents are unknowable, so treat it as private.
        'other'
    )
$$ LANGUAGE sql IMMUTABLE;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Remove what was already collected
-- ─────────────────────────────────────────────────────────────────────────────
-- Stations first, so their aliases and OSM objects cascade. There are none at the time of
-- writing, but one registered by hand before this ran must not survive it.
DELETE FROM stations
WHERE NOT station_type_tracked(station_type);

-- Then the labels themselves: the rows that carry the private text.
DELETE FROM station_labels
WHERE NOT station_type_tracked(station_type);

-- trip_station_endpoints (0060) is defined in terms of station_type_tracked(), so it now
-- excludes these modes automatically and rebuild_labels() will not bring them back.

ANALYZE station_labels;
ANALYZE stations;
