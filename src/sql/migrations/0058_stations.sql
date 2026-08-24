-- Canonical stations, the places trips actually start and end at.
--
-- A trip's endpoints were only their printed label, so one physical station became as many
-- "stations" as there were spellings of it, and every aggregate grouping on the string counted
-- them apart. This is the registry those labels resolve to. Following operator_aliases (0042)
-- and vessels (0054), `trips.origin_station` stays free text and the source of truth, and
-- resolution happens at read time — so identifying a station later fixes every trip that ever
-- named it.
--
-- Seeded LAZILY, when somebody first picks a station: the answer to the maintenance problem
-- that killed the hand-curated list, whose 123k rows nobody could keep current.


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Normalisation
-- ─────────────────────────────────────────────────────────────────────────────
-- Fold a written station name to a comparison key. Mirrors operator_normalize() (0042), plus
-- stripping the leading flag emoji, which must not take part in matching.
--
-- [:alnum:] rather than [a-z0-9] is deliberate: the class is ctype-aware, so Київ, 東京 and
-- Αθήνα keep their letters instead of normalising to the empty string.
--
-- The schema qualification is load-bearing: CREATE INDEX evaluates its expression under a
-- restricted search_path, so an unqualified version fails there while working in a SELECT.
CREATE OR REPLACE FUNCTION station_normalize(text) RETURNS text AS $$
    SELECT NULLIF(
        lower(regexp_replace(
            public.unaccent('public.unaccent'::regdictionary, COALESCE($1, '')),
            '[^[:alnum:]]+', '', 'g'
        )),
        ''
    )
$$ LANGUAGE sql IMMUTABLE;

-- Diacritic-folded lowercase, for fuzzy matching: "munchen" must find "München".
-- Schema-qualified for the same index-expression reason as station_normalize() above.
CREATE OR REPLACE FUNCTION station_fold(text) RETURNS text AS $$
    SELECT lower(public.unaccent('public.unaccent'::regdictionary, COALESCE($1, '')))
$$ LANGUAGE sql IMMUTABLE;

-- The country code carried by a label's leading flag emoji, or NULL. A flag is two regional
-- indicator symbols (U+1F1E6..U+1F1FF) standing for 'A'..'Z', so "🇫🇷 Gare de Lyon" is 'FR'.
--
-- A hint, not a fact: measured, "Paris Gare du Nord" appears with 🇬🇭 and 🇬🇧 alongside 🇫🇷,
-- "Rotterdam Centraal" with 🇧🇪. Resolution prefers on it, never excludes on it.
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

-- Which pool of places a trip type draws from. Mirrors station_bucket() in src/stations.py,
-- which carries the reasoning.
CREATE OR REPLACE FUNCTION station_type_bucket(trip_type text) RETURNS text AS $$
    SELECT CASE
        WHEN $1 IN ('accommodation', 'accomodation') THEN 'accommodation'
        WHEN $1 IS NULL OR $1 = '' THEN 'other'
        ELSE $1
    END
$$ LANGUAGE sql IMMUTABLE;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. The registry
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE stations (
    station_id    SERIAL PRIMARY KEY,

    -- The OSM object a user picked. Not the identity: see wikidata/uic_ref below and
    -- station_osm_objects for why one station has several of these.
    osm_type      CHAR(1),
    osm_id        BIGINT,

    -- The real identity anchors, and the reason this table is worth having. Measured over 60
    -- stations: wikidata on 97%, uic_ref on 35%. Both are shared by every OSM object of one
    -- station and both survive the object churn that changes osm_id, which makes them better
    -- identity than (osm_type, osm_id) — so resolution prefers them, in this order.
    wikidata      TEXT,
    uic_ref       TEXT,

    station_type  TEXT NOT NULL,

    -- name_local is the OSM `name`; name_intl is what src/station_names.py made of it;
    -- `names` holds every name:* / int_name / alt_name tag verbatim. All three are stored so
    -- that display is a rendering decision and never a re-fetch.
    name_local    TEXT,
    name_intl     TEXT NOT NULL,
    names         JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- An admin's correction, outranking name_intl everywhere. The escape hatch for what the
    -- naming rules cannot settle: BGN gives "Kyyiv-Pasazhyrskyy" where the accepted spelling
    -- is "Kyiv-Pasazhyrskyi", and bilingual places have no single right answer.
    curated_name  TEXT,

    country_code  TEXT,

    -- Where OSM puts the object, and where an admin says the station actually is.
    --
    -- A station node is often placed on whichever track the mapper was tracing — the train
    -- station's node out on the tram tracks, or on the metro platform below. Fine for OSM and
    -- wrong here: these coordinates anchor the geometry backfill, the duplicate radius and the
    -- sibling lookup's `around` bound, so a node on the wrong tracks breaks all three.
    --
    -- The OSM value is kept rather than overwritten, so re-enrichment can refresh it without
    -- destroying the correction.
    lat           DOUBLE PRECISION,
    lng           DOUBLE PRECISION,
    curated_lat   DOUBLE PRECISION,
    curated_lng   DOUBLE PRECISION,

    -- What every reader should use. Generated rather than left to each caller to COALESCE:
    -- a caller that forgets silently uses the position the correction exists to fix.
    effective_lat DOUBLE PRECISION GENERATED ALWAYS AS (COALESCE(curated_lat, lat)) STORED,
    effective_lng DOUBLE PRECISION GENERATED ALWAYS AS (COALESCE(curated_lng, lng)) STORED,

    -- NULL means the tags are not fetched yet: this column is the enrichment queue, and why
    -- a trip save never waits on a third-party API.
    enriched_at   TIMESTAMPTZ,

    -- Set when this station is folded into another. Reads follow it; nothing is deleted, so
    -- trips pointing here keep working.
    superseded_by INTEGER REFERENCES stations (station_id)
);

-- Identity, strongest first. Partial so the majority of rows with no uic_ref do not all
-- collide on NULL.
CREATE UNIQUE INDEX stations_wikidata_key ON stations (station_type, wikidata)
    WHERE wikidata IS NOT NULL;
CREATE UNIQUE INDEX stations_uic_ref_key ON stations (station_type, uic_ref)
    WHERE uic_ref IS NOT NULL;
CREATE UNIQUE INDEX stations_osm_key ON stations (osm_type, osm_id)
    WHERE osm_id IS NOT NULL;

CREATE INDEX stations_enrichment_queue_idx ON stations (station_id)
    WHERE enriched_at IS NULL;
CREATE INDEX stations_superseded_by_idx ON stations (superseded_by)
    WHERE superseded_by IS NOT NULL;
-- The geometry backfill asks "stations near this point", so it is indexed on the effective
-- coordinates rather than the raw ones.
CREATE INDEX stations_coords_idx ON stations (effective_lat, effective_lng);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Which OSM objects are this station
-- ─────────────────────────────────────────────────────────────────────────────
-- Photon indexes the station node, the building way, the stop_area relation and every platform
-- separately, so one station comes back several times under different osm_ids — measured,
-- Antwerpen-Centraal six times and München Hbf five — and two users will not pick the same one.
--
-- Recording every object of a station makes that harmless: whichever is picked resolves to the
-- same row, and the autocomplete collapses duplicates exactly instead of guessing by distance.
CREATE TABLE station_osm_objects (
    osm_type   CHAR(1) NOT NULL,
    osm_id     BIGINT  NOT NULL,
    station_id INTEGER NOT NULL REFERENCES stations (station_id) ON DELETE CASCADE,
    PRIMARY KEY (osm_type, osm_id)
);
CREATE INDEX station_osm_objects_station_id_idx ON station_osm_objects (station_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Aliases
-- ─────────────────────────────────────────────────────────────────────────────
-- Every spelling a station is known by: its own names, every name:* tag, alt_name, int_name,
-- and anything an admin adds.
--
-- Deliberately unlike operator_aliases (0042), which puts a UNIQUE index on the normalised
-- spelling because one spelling means one operator. False for stations — "Hauptbahnhof" and
-- "Centraal" name hundreds of places — so a spelling may point at many stations and
-- resolution must handle that. See station_resolve_alias() in 0060.
--
-- This is also what fixes searchability: Photon indexes only default/de/en/fr, so a station
-- named in Finnish cannot be found by a Finn. Enrichment writes every name:* here.
CREATE TABLE station_aliases (
    alias_id   SERIAL PRIMARY KEY,
    station_id INTEGER NOT NULL REFERENCES stations (station_id) ON DELETE CASCADE,
    alias      TEXT NOT NULL,
    normalized TEXT GENERATED ALWAYS AS (station_normalize(alias)) STORED,
    -- Where the spelling came from. 'lang' rows carry the language in `lang`.
    kind       TEXT NOT NULL DEFAULT 'alias',
    lang       TEXT,
    CONSTRAINT station_aliases_kind_check
        CHECK (kind IN ('intl', 'local', 'int_name', 'alt_name', 'official', 'lang', 'alias')),
    -- A spelling that normalises to nothing would match everything.
    CONSTRAINT station_aliases_normalized_check CHECK (normalized IS NOT NULL)
);

-- One row per spelling per station; the same spelling on two stations is allowed.
CREATE UNIQUE INDEX station_aliases_station_normalized_key
    ON station_aliases (station_id, normalized);
-- Exact resolution: raw trip text -> candidate stations.
CREATE INDEX station_aliases_normalized_idx ON station_aliases (normalized);
-- Fuzzy search, on the folded form so "munchen" finds "München". Queries must fold their
-- search term with station_fold() too, or they will not use this index.
CREATE INDEX station_aliases_alias_trgm_idx
    ON station_aliases USING gin (station_fold(alias) gin_trgm_ops);

-- The planner needs statistics before the first autocomplete query after deploy.
ANALYZE stations;
ANALYZE station_aliases;
