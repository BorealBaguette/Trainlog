-- The read path for the station registry, plus the pieces 0058/0059 duplicated.
--
-- 0058 and 0059 built the registry and the label cache but nothing ever read them: every
-- aggregate still grouped on `trips.origin_station` as raw text, so a station logged under
-- two spellings still counted twice, and an admin's curated_name changed nothing anybody
-- could see. The functions below are what close that, and stats_stations / stats_routes /
-- unique_stations now go through them.
--
-- Three duplications from 0058/0059 are collapsed here at the same time, because the read
-- path would otherwise have added a fourth copy of each:
--
--   * the origin/destination UNION ALL, written out four times  -> trip_station_endpoints
--   * the ambiguity CASE, written out three times               -> station_resolve_alias()
--   * the 'air' exclusion, hardcoded at five call sites         -> station_type_tracked()


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Which trip types the registry tracks
-- ─────────────────────────────────────────────────────────────────────────────
-- Mirrors REGISTRY_EXCLUDED_TYPES in src/stations.py, which states the rule and the reasons.
-- check_labels_consistency() compares the two over every trip type in the data, so a
-- divergence is reported rather than left to be discovered.
CREATE OR REPLACE FUNCTION station_type_tracked(trip_type text) RETURNS boolean AS $$
    SELECT station_type_bucket($1) <> 'air'
$$ LANGUAGE sql IMMUTABLE;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. One definition of a trip's endpoints
-- ─────────────────────────────────────────────────────────────────────────────
-- Four callers each carried their own copy of this UNION ALL, and the count refresh
-- disagreeing with the seed would silently mis-order the admin queue.
--
-- 0059's expression indexes still apply through the view, so callers filtering on
-- `normalized` get an index lookup rather than a scan.
CREATE OR REPLACE VIEW trip_station_endpoints AS
SELECT trip_id,
       user_id,
       station_normalize(origin_station)  AS normalized,
       station_type_bucket(trip_type)     AS station_type,
       origin_station                     AS raw
FROM trips
WHERE station_normalize(origin_station) IS NOT NULL
  AND station_type_tracked(trip_type)
UNION ALL
SELECT trip_id,
       user_id,
       station_normalize(destination_station),
       station_type_bucket(trip_type),
       destination_station
FROM trips
WHERE station_normalize(destination_station) IS NOT NULL
  AND station_type_tracked(trip_type);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. The ambiguity rule, once
-- ─────────────────────────────────────────────────────────────────────────────
-- A spelling can legitimately name several stations (see station_aliases in 0058). The rule:
--
--   1. Exactly one candidate whose country matches the label's flag  -> that one.
--   2. Otherwise, exactly one candidate at all                       -> that one.
--   3. Otherwise                                                     -> NULL, for a human.
--
-- Guessing between two real stations would attribute a trip to a place the user never went,
-- which is worse than leaving the spelling unresolved. The flag only ever breaks ties; it is
-- too unreliable to filter on (see station_flag_country in 0058).
--
-- Previously written out three times; all three callers now use this.
CREATE OR REPLACE FUNCTION station_resolve_alias(
    p_normalized text, p_station_type text, p_flag_country text
) RETURNS integer AS $$
    SELECT CASE
        WHEN count(*) FILTER (WHERE c.country_matches) = 1
            THEN (array_agg(c.station_id) FILTER (WHERE c.country_matches))[1]
        WHEN count(*) = 1
            THEN (array_agg(c.station_id))[1]
        ELSE NULL
    END
    FROM (
        SELECT s.station_id,
               s.country_code IS NOT DISTINCT FROM p_flag_country AS country_matches
        FROM station_aliases a
        JOIN stations s ON s.station_id = a.station_id
        WHERE a.normalized = p_normalized
          AND s.station_type = p_station_type
          -- A merged-away station must not capture labels; its survivor carries the
          -- spelling, because merge moves the aliases across.
          AND s.superseded_by IS NULL
    ) c
$$ LANGUAGE sql STABLE;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. What a station is called, for a given reader
-- ─────────────────────────────────────────────────────────────────────────────
-- The leading flag emoji of a label with its trailing space, or ''. Replacing a label with the
-- registry's name would otherwise drop the flag from every stats row.
CREATE OR REPLACE FUNCTION station_flag_prefix(text) RETURNS text AS $$
    SELECT CASE
        WHEN station_flag_country($1) IS NULL THEN ''
        ELSE substring($1 FROM 1 FOR 2) || ' '
    END
$$ LANGUAGE sql IMMUTABLE;

-- The name to show for a station, given a reader's display preference. Mirrors display_name()
-- in src/stations.py, which carries the rules — keep the two in step.
--
--   'international'  the generally-accepted name (München Hbf, Kyiv-Pasazhyrskyi)
--   'native'         the OSM `name`, whatever script it is in (東京駅, Київ-Пасажирський)
--   'language'       the reader's own language where the station has a name in it
--
-- Two forms of one rule. The columns form is the rule; the id form looks the row up and calls
-- it. Both exist because an aggregate has already joined `stations`, so making it call the id
-- form is a second lookup of a row it is holding — measured at 7.7s for one user's station
-- stats against 84ms. A caller holding only an id has nothing to pass and gets the wrapper.
CREATE OR REPLACE FUNCTION station_display_name(
    p_curated text, p_name_intl text, p_name_local text, p_names jsonb,
    p_mode text, p_lang text
) RETURNS text AS $$
    SELECT COALESCE(
        p_curated,
        CASE
            WHEN p_mode = 'native' THEN p_name_local
            WHEN p_mode = 'language' AND p_lang IS NOT NULL AND p_lang <> '' THEN
                COALESCE(p_names ->> ('name:' || p_lang),
                         p_names ->> ('name:' || split_part(p_lang, '-', 1)))
        END,
        p_name_intl
    )
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION station_display_name(
    p_station_id integer, p_mode text, p_lang text
) RETURNS text AS $$
    SELECT station_display_name(
        s.curated_name, s.name_intl, s.name_local, s.names, p_mode, p_lang
    )
    FROM stations s
    WHERE s.station_id = p_station_id
$$ LANGUAGE sql STABLE;

-- What a written label should be displayed as: the registry's name for the station it resolves
-- to, or the label itself. For single rows and small result sets only.
--
-- NOT for aggregates: this hides a correlated subquery per call, and a stats query calls it
-- twice per trip — 15s for one user's routes against 34ms grouping on raw text. An aggregate
-- should join station_labels and stations once and call the columns form of
-- station_display_name(); stats_stations.sql and stats_routes.sql show the shape.
CREATE OR REPLACE FUNCTION station_label_display(
    p_label text, p_trip_type text, p_mode text, p_lang text
) RETURNS text AS $$
    SELECT COALESCE(
        (
            SELECT station_flag_prefix(p_label)
                   || station_display_name(sl.station_id, p_mode, p_lang)
            FROM station_labels sl
            WHERE sl.normalized   = station_normalize(p_label)
              AND sl.station_type = station_type_bucket(p_trip_type)
              AND sl.station_id IS NOT NULL
        ),
        p_label
    )
$$ LANGUAGE sql STABLE;


-- The identity an aggregate should group a written label under. Separate from
-- station_label_display() because two different stations can share a name, and grouping on the
-- displayed name would merge them: two spellings of one station must collapse, two stations
-- with one name must not.
--
-- Resolved labels key on the station id, unresolved ones on their normalised spelling, and the
-- raw label is the last resort for a spelling that normalises to nothing.
--
-- Same warning as station_label_display(): fine per row, too slow per aggregate.
CREATE OR REPLACE FUNCTION station_label_key(p_label text, p_trip_type text)
RETURNS text AS $$
    SELECT COALESCE(
        (
            SELECT '#' || sl.station_id::text
            FROM station_labels sl
            WHERE sl.normalized   = station_normalize(p_label)
              AND sl.station_type = station_type_bucket(p_trip_type)
              AND sl.station_id IS NOT NULL
        ),
        station_normalize(p_label),
        p_label
    )
$$ LANGUAGE sql STABLE;


-- The index the read path joins through. station_labels_key (0059) covers every spelling, but
-- an aggregate only wants the resolved ones — a small minority, 61 rows out of 141,633, since
-- the table holds one row per spelling ever typed and the registry is seeded lazily.
--
-- Without this the planner hashes all 141,633 rows, twice for a routes query, and spills to
-- disk: 470ms against 33ms. This partial index grows with the number of registered stations
-- instead of the number of spellings.
CREATE INDEX station_labels_resolved_key
    ON station_labels (station_type, normalized)
    INCLUDE (station_id)
    WHERE station_id IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. The reference table the registry replaced
-- ─────────────────────────────────────────────────────────────────────────────
-- train_stations (0026) backed /trainStationAutocomplete, which is gone: station search is
-- Photon plus the registry now. 123k rows, no remaining reader, reproducible from
-- base_data/train_stations.csv if it is ever wanted back.
DROP TABLE IF EXISTS train_stations;
