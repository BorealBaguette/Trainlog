-- Station registry. Trip endpoints stay free text and remain the source of truth;
-- station_labels maps each distinct spelling to a station, resolved at read time, so
-- identifying a station later fixes every trip that ever named it.


-- ─────────────────────────────────────────────────────────────────────────────
-- Helpers
-- ─────────────────────────────────────────────────────────────────────────────

-- Comparison key for a written name: no flag, case, accents or punctuation.
-- [:alnum:] rather than [a-z0-9] so non-Latin names (Київ, 東京) keep their letters.
-- Schema-qualified because index expressions are evaluated under a restricted search_path.
CREATE OR REPLACE FUNCTION station_normalize(text) RETURNS text AS $$
    SELECT NULLIF(
        lower(regexp_replace(
            public.unaccent('public.unaccent'::regdictionary, COALESCE($1, '')),
            '[^[:alnum:]]+', '', 'g'
        )),
        ''
    )
$$ LANGUAGE sql IMMUTABLE;

-- Folded form for fuzzy search, so "munchen" finds "München".
CREATE OR REPLACE FUNCTION station_fold(text) RETURNS text AS $$
    SELECT lower(public.unaccent('public.unaccent'::regdictionary, COALESCE($1, '')))
$$ LANGUAGE sql IMMUTABLE;

-- Country code of a label's leading flag emoji, or NULL. Stored flags are often wrong,
-- so this only ever breaks ties, never filters.
CREATE OR REPLACE FUNCTION station_flag_country(text) RETURNS text AS $$
    SELECT CASE
        WHEN $1 IS NULL OR length($1) < 2 THEN NULL
        WHEN ascii(substring($1 FROM 1 FOR 1)) BETWEEN 127462 AND 127487
         AND ascii(substring($1 FROM 2 FOR 1)) BETWEEN 127462 AND 127487
        THEN chr(ascii(substring($1 FROM 1 FOR 1)) - 127462 + 65)
          || chr(ascii(substring($1 FROM 2 FOR 1)) - 127462 + 65)
        ELSE NULL
    END
$$ LANGUAGE sql IMMUTABLE;

-- The label's flag with its trailing space, or ''.
CREATE OR REPLACE FUNCTION station_flag_prefix(text) RETURNS text AS $$
    SELECT CASE
        WHEN station_flag_country($1) IS NULL THEN ''
        ELSE substring($1 FROM 1 FOR 2) || ' '
    END
$$ LANGUAGE sql IMMUTABLE;

-- Mirrors station_bucket() in src/stations.py.
CREATE OR REPLACE FUNCTION station_type_bucket(trip_type text) RETURNS text AS $$
    SELECT CASE
        WHEN $1 IN ('accommodation', 'accomodation') THEN 'accommodation'
        WHEN $1 IS NULL OR $1 = '' THEN 'other'
        ELSE $1
    END
$$ LANGUAGE sql IMMUTABLE;

-- Mirrors REGISTRY_EXCLUDED_TYPES in src/stations.py, which gives the reasons.
CREATE OR REPLACE FUNCTION station_type_tracked(trip_type text) RETURNS boolean AS $$
    SELECT station_type_bucket($1) NOT IN (
        'air',
        'car', 'walk', 'cycle', 'scooter',
        'accommodation', 'restaurant', 'poi',
        'other'
    )
$$ LANGUAGE sql IMMUTABLE;


-- ─────────────────────────────────────────────────────────────────────────────
-- Registry
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE stations (
    station_id    SERIAL PRIMARY KEY,

    -- The OSM object first picked. Identity prefers wikidata, then uic_ref: both are
    -- shared by every object of a station and survive OSM object churn.
    osm_type      CHAR(1),
    osm_id        BIGINT,
    wikidata      TEXT,
    uic_ref       TEXT,

    station_type  TEXT NOT NULL,

    -- name_local is the OSM `name`, name_intl the name chosen by src/station_names.py,
    -- names every name:* / int_name / alt_name tag. curated_name is an admin override.
    name_local    TEXT,
    name_intl     TEXT NOT NULL,
    names         JSONB NOT NULL DEFAULT '{}'::jsonb,
    curated_name  TEXT,

    country_code  TEXT,

    -- OSM often places a station node on the wrong tracks; the admin correction lives
    -- beside it so re-enrichment can refresh the OSM position without losing it.
    lat           DOUBLE PRECISION,
    lng           DOUBLE PRECISION,
    curated_lat   DOUBLE PRECISION,
    curated_lng   DOUBLE PRECISION,
    effective_lat DOUBLE PRECISION GENERATED ALWAYS AS (COALESCE(curated_lat, lat)) STORED,
    effective_lng DOUBLE PRECISION GENERATED ALWAYS AS (COALESCE(curated_lng, lng)) STORED,

    -- NULL = waiting for OSM enrichment; this column is the queue.
    enriched_at   TIMESTAMPTZ,

    -- Set when merged into another station. Reads follow it.
    superseded_by INTEGER REFERENCES stations (station_id)
);

-- Identity is unique per mode among live stations only: a merged-away row must not
-- block its survivor from taking the same anchor.
CREATE UNIQUE INDEX stations_wikidata_key ON stations (station_type, wikidata)
    WHERE wikidata IS NOT NULL AND superseded_by IS NULL;
CREATE UNIQUE INDEX stations_uic_ref_key ON stations (station_type, uic_ref)
    WHERE uic_ref IS NOT NULL AND superseded_by IS NULL;
CREATE UNIQUE INDEX stations_osm_key ON stations (station_type, osm_type, osm_id)
    WHERE osm_id IS NOT NULL AND superseded_by IS NULL;

CREATE INDEX stations_enrichment_queue_idx ON stations (station_id)
    WHERE enriched_at IS NULL;
CREATE INDEX stations_superseded_by_idx ON stations (superseded_by)
    WHERE superseded_by IS NOT NULL;
CREATE INDEX stations_coords_idx ON stations (effective_lat, effective_lng);

-- Photon returns a station's node, building, stop_area and platforms as separate results,
-- so every OSM object of a station maps to it. Per mode: at an interchange one node can
-- anchor both the metro stop and the train station.
CREATE TABLE station_osm_objects (
    osm_type   CHAR(1) NOT NULL,
    osm_id     BIGINT  NOT NULL,
    station_id INTEGER NOT NULL REFERENCES stations (station_id) ON DELETE CASCADE,
    PRIMARY KEY (osm_type, osm_id, station_id)
);
CREATE INDEX station_osm_objects_station_id_idx ON station_osm_objects (station_id);

-- Every spelling a station is known by. Not unique across stations: "Hauptbahnhof" names
-- hundreds of them, and station_resolve_alias() handles the ambiguity.
CREATE TABLE station_aliases (
    alias_id   SERIAL PRIMARY KEY,
    station_id INTEGER NOT NULL REFERENCES stations (station_id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,
    normalized TEXT GENERATED ALWAYS AS (station_normalize(alias)) STORED,
    kind       TEXT NOT NULL DEFAULT 'alias',
    lang       TEXT,
    CONSTRAINT station_aliases_kind_check
        CHECK (kind IN ('intl', 'local', 'int_name', 'alt_name', 'official', 'lang', 'alias')),
    -- A spelling that normalises to nothing would match everything.
    CONSTRAINT station_aliases_normalized_check CHECK (normalized IS NOT NULL)
);

CREATE UNIQUE INDEX station_aliases_station_normalized_key
    ON station_aliases (station_id, normalized);
CREATE INDEX station_aliases_normalized_idx ON station_aliases (normalized);
-- Queries must fold their search term with station_fold() to use this.
CREATE INDEX station_aliases_alias_trgm_idx
    ON station_aliases USING gin (station_fold(alias) gin_trgm_ops);


-- ─────────────────────────────────────────────────────────────────────────────
-- Label cache
-- ─────────────────────────────────────────────────────────────────────────────

-- What each distinct written spelling resolves to, per mode. Keyed on the spelling rather
-- than the trip, so editing a trip needs no sync. station_id NULL is the admin queue.
CREATE TABLE station_labels (
    label_id        SERIAL PRIMARY KEY,
    normalized      TEXT NOT NULL,
    station_type    TEXT NOT NULL,
    sample_label    TEXT NOT NULL,
    station_id      INTEGER REFERENCES stations (station_id) ON DELETE SET NULL,
    -- Refreshed in bulk by refresh_label_counts(); they only order the queue.
    occurrences     INTEGER NOT NULL DEFAULT 0,
    users           INTEGER NOT NULL DEFAULT 0,
    -- What the seeding script concluded; see find_candidate() in src/station_seed.py.
    auto_checked_at TIMESTAMPTZ,
    auto_result     TEXT,
    CONSTRAINT station_labels_normalized_check CHECK (normalized <> '')
);

CREATE UNIQUE INDEX station_labels_key ON station_labels (station_type, normalized);
CREATE INDEX station_labels_station_id_idx ON station_labels (station_id);
CREATE INDEX station_labels_unresolved_idx ON station_labels (occurrences DESC)
    WHERE station_id IS NULL;
CREATE INDEX station_labels_auto_result_idx ON station_labels (auto_result)
    WHERE station_id IS NULL;
-- Aggregates only join resolved labels, a small minority of the table.
CREATE INDEX station_labels_resolved_key
    ON station_labels (station_type, normalized)
    INCLUDE (station_id)
    WHERE station_id IS NOT NULL;

CREATE INDEX trips_origin_station_normalized_idx
    ON trips (station_normalize(origin_station));
CREATE INDEX trips_destination_station_normalized_idx
    ON trips (station_normalize(destination_station));

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

INSERT INTO station_labels (normalized, station_type, sample_label, occurrences, users)
SELECT normalized,
       station_type,
       (array_agg(raw ORDER BY n DESC))[1],
       sum(n)::int,
       max(u)::int
FROM (
    SELECT normalized, station_type, raw,
           count(*) AS n, count(DISTINCT user_id) AS u
    FROM trip_station_endpoints
    GROUP BY 1, 2, 3
) spellings
GROUP BY normalized, station_type;


-- ─────────────────────────────────────────────────────────────────────────────
-- Resolution and display
-- ─────────────────────────────────────────────────────────────────────────────

-- The single station a spelling names, or NULL for a human to decide:
--   1. exactly one candidate in the label's flag country
--   2. otherwise exactly one candidate
-- Guessing between two real stations would silently credit trips to the wrong place.
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
          AND s.superseded_by IS NULL
    ) c
$$ LANGUAGE sql STABLE;

-- Mirrors display_name() in src/stations.py. Takes columns rather than an id because
-- aggregates have already joined `stations`.
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


-- ─────────────────────────────────────────────────────────────────────────────
-- Seeding runs started from the admin panel
-- ─────────────────────────────────────────────────────────────────────────────

-- In the database because the browser's progress poll can land on any worker.
-- updated_at is the heartbeat that tells a dead run from a slow one.
CREATE TABLE station_seed_runs (
    run_id           SERIAL PRIMARY KEY,
    started_by       TEXT NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ,
    params           JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- running | done | stopped | failed
    state            TEXT NOT NULL DEFAULT 'running',
    stop_requested   BOOLEAN NOT NULL DEFAULT FALSE,
    total            INTEGER NOT NULL DEFAULT 0,
    attempted        INTEGER NOT NULL DEFAULT 0,
    registered       INTEGER NOT NULL DEFAULT 0,
    skipped          INTEGER NOT NULL DEFAULT 0,
    incomplete       INTEGER NOT NULL DEFAULT 0,
    failed           INTEGER NOT NULL DEFAULT 0,
    endpoints_gained INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    log              JSONB NOT NULL DEFAULT '[]'::jsonb
);


-- Replaced by Photon plus this registry.
DROP TABLE IF EXISTS train_stations;

ANALYZE stations;
ANALYZE station_aliases;
ANALYZE station_labels;
