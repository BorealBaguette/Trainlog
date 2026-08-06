-- Vessel identity, split from the photo cache.
--
-- Until now a ship was identified by one free-text string: `ship_pictures.vessel_name`,
-- matched against `trips.reg` by exact equality. That string is whatever the user
-- happened to type, so one ship is stored several times over — 'Megastar',
-- 'M/S Megastar', '9773064' (its IMO) and ' 9892690' are all the same Tallink ferry —
-- and the stats chart draws each spelling as its own bar, half of them numbers.
--
-- This migration introduces `vessels`: name, IMO and MMSI as three separate columns of
-- one row. Any of the three resolves to that row (vessel_resolve), and the display
-- layer always shows the name (vessel_display_name), whichever the user typed.
--
-- `trips.reg` stays free text and is still what the user typed — exactly as
-- `trips.operator` stayed free text when operator_aliases arrived (0042). Nothing
-- rewrites a user's trips; resolution happens at read time, so a vessel identified
-- later fixes every trip that ever named it, retroactively.


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Normalisation
-- ─────────────────────────────────────────────────────────────────────────────
-- Fold a written vessel name to a comparison key. Two things happen:
--
--   * a leading ship-type prefix is dropped, because it is a title and not part of
--     the name: 'M/S Megastar', 'MS Megastar' and 'Megastar' are one ship, and the
--     logged spellings genuinely disagree ('M/S LADEJARL' vs 'Ladejarl'). The
--     separator after the prefix is required, so 'Mystar' and 'Msida' keep theirs.
--   * everything that is not alphanumeric is removed and the rest lowercased, as in
--     operator_normalize: 'IJVEER 51', 'IJveer-51' and 'ijveer 51' agree.
--
-- [:alnum:] rather than [a-z0-9] on purpose — the class is ctype-aware, so Cyrillic
-- and Greek names survive instead of normalising to the empty string. IMMUTABLE so it
-- can back the stored generated column below; that needs the two-argument unaccent().
CREATE OR REPLACE FUNCTION vessel_normalize(text) RETURNS text AS $$
    SELECT NULLIF(
        regexp_replace(
            lower(
                regexp_replace(
                    unaccent('unaccent', COALESCE($1, '')),
                    '^\s*(m/?s|m/?f|m/?v|s/?s|d/?s|f/?b|t/?s|hsc|hss)[\s./_-]+',
                    '',
                    'i'
                )
            ),
            '[^[:alnum:]]+', '', 'g'
        ),
        ''
    )
$$ LANGUAGE sql IMMUTABLE;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. The table
-- ─────────────────────────────────────────────────────────────────────────────
-- IMO is a 7-digit hull number, permanent for the life of the ship. MMSI is a
-- 9-digit radio identity that changes with the flag state, and whose first three
-- digits are the MID that already drives the flag (see mids.json). Both are stored as
-- text: they are codes, not quantities, and leading zeros are meaningful in an MMSI.
--
-- All three identifiers are nullable — a ship known only by name is the common case,
-- and a photo fetched by number may never yield a name — but a row with none of them
-- identifies nothing, hence the check.
--
-- `name` is deliberately NOT unique: real ships share names (there are several
-- 'Express'), and the alternative would be to invent a distinction the data does not
-- have. vessel_resolve() picks deterministically instead.
CREATE TABLE vessels (
    uid          SERIAL PRIMARY KEY,
    name         TEXT,
    name_key     TEXT GENERATED ALWAYS AS (vessel_normalize(name)) STORED,
    imo          TEXT,
    mmsi         TEXT,
    country_code TEXT,
    notes        TEXT,
    updated_on   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT vessels_imo_format  CHECK (imo IS NULL OR imo ~ '^[0-9]{7}$'),
    CONSTRAINT vessels_mmsi_format CHECK (mmsi IS NULL OR mmsi ~ '^[0-9]{9}$'),
    CONSTRAINT vessels_identified  CHECK (name_key IS NOT NULL OR imo IS NOT NULL OR mmsi IS NOT NULL)
);

CREATE UNIQUE INDEX vessels_imo_idx ON vessels (imo) WHERE imo IS NOT NULL;
CREATE UNIQUE INDEX vessels_mmsi_idx ON vessels (mmsi) WHERE mmsi IS NOT NULL;
CREATE INDEX vessels_name_key_idx ON vessels (name_key) WHERE name_key IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Resolution
-- ─────────────────────────────────────────────────────────────────────────────
-- One written identifier -> one vessel. A number is tried as an IMO and as an MMSI
-- (the digit count usually decides, but nothing guarantees the user typed a
-- well-formed one), and anything else is matched on the folded name.
--
-- Number matches outrank a name match, and the exact identifiers outrank each other
-- in the order IMO, MMSI: the IMO is the permanent one. Ties fall back to the lowest
-- uid so the answer never depends on plan order.
--
-- STABLE, not IMMUTABLE: it reads a table. That rules it out of an index, which is
-- fine — `vessels` is a few hundred rows and every call is an index lookup on it.
CREATE OR REPLACE FUNCTION vessel_resolve(text) RETURNS integer AS $$
    SELECT v.uid
    FROM vessels v
    WHERE v.imo = btrim($1)
       OR v.mmsi = btrim($1)
       OR (v.name_key IS NOT NULL AND v.name_key = vessel_normalize($1))
    ORDER BY (v.imo = btrim($1)) DESC NULLS LAST,
             (v.mmsi = btrim($1)) DESC NULLS LAST,
             v.uid
    LIMIT 1
$$ LANGUAGE sql STABLE;

-- What to show for a written identifier: the vessel's name if we know one, otherwise
-- the text itself. This is the whole point of the split — a trip logged as '9773064'
-- reads 'Megastar' everywhere without its `reg` being touched.
--
-- `fold_unknown` decides what happens to text that resolves to nothing. Displaying a
-- trip leaves it exactly as typed (false); grouping it in stats upper-cases it (true),
-- which is what the vehicles chart did to every registration before this migration and
-- is what keeps 'M/S Ladejarl' and 'M/S LADEJARL' on one bar while they stay unknown.
CREATE OR REPLACE FUNCTION vessel_display_name(text, boolean DEFAULT false) RETURNS text AS $$
    SELECT COALESCE(
        (SELECT NULLIF(btrim(v.name), '') FROM vessels v WHERE v.uid = vessel_resolve($1)),
        CASE WHEN $2 THEN upper(btrim($1)) ELSE btrim($1) END
    )
$$ LANGUAGE sql STABLE;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. ship_pictures becomes a photo cache hanging off a vessel
-- ─────────────────────────────────────────────────────────────────────────────
-- `vessel_name` is kept: it records the term that was searched, which is the only
-- provenance the cached file has. It is no longer the ship's identity, and nothing
-- should match against it — go through vessel_id.
ALTER TABLE ship_pictures ADD COLUMN vessel_id INTEGER REFERENCES vessels(uid) ON DELETE CASCADE;
CREATE INDEX ship_pictures_vessel_id_idx ON ship_pictures (vessel_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Backfill
-- ─────────────────────────────────────────────────────────────────────────────
-- Everything below reads only `ship_pictures`. Three facts are recoverable from it:
--
--   * the MMSI, from the cached filename. get_vessel_picture has always parsed it out
--     of the vesselfinder URL and baked it into '{country}_{name}_{mmsi}.jpg' without
--     ever storing it — 607 of the 614 rows carry one, which makes MMSI the key that
--     actually merges the duplicate spellings.
--   * the IMO, when the searched term was one (95 rows are a bare 7-digit number) or
--     when it was written alongside the name ('IMO: 9235517, Stena Scandinavica').
--   * the name, once any such 'IMO 1234567' fragment is stripped off and provided what
--     is left is not itself just digits.
CREATE TEMP TABLE vessel_import ON COMMIT DROP AS
WITH cleaned AS (
    SELECT
        sp.uid,
        sp.country_code,
        sp.fetch_date,
        btrim(sp.vessel_name) AS raw,
        (regexp_match(sp.local_image_path, '_([0-9]{9})\.jpg$'))[1] AS file_mmsi,
        -- Capturing group, not the non-capturing form: migrations run through
        -- SQLAlchemy's text(), which reads a question mark followed by a colon and a
        -- word as a bind parameter, quoted string or not.
        (regexp_match(sp.vessel_name, '(imo|omi)[^0-9]{0,12}([0-9]{7})', 'i'))[2] AS text_imo,
        btrim(regexp_replace(
            COALESCE(sp.vessel_name, ''),
            '(imo|omi)[^0-9]{0,12}[0-9]{7}[\s,:;-]*', '', 'gi'
        )) AS text_name
    FROM ship_pictures sp
),
typed AS (
    SELECT
        uid,
        country_code,
        fetch_date,
        CASE WHEN raw ~ '^[0-9]{7}$' THEN raw ELSE text_imo END AS imo,
        COALESCE(file_mmsi, CASE WHEN raw ~ '^[0-9]{9}$' THEN raw END) AS mmsi,
        -- A name that is nothing but digits is an identifier the search happened to
        -- be handed, not a name. Those rows keep their number and stay nameless until
        -- someone fills one in.
        NULLIF(CASE WHEN text_name ~ '^[0-9]*$' THEN '' ELSE text_name END, '') AS name
    FROM cleaned
)
SELECT
    typed.*,
    -- One ship = one MMSI. Rows without one fall back to their IMO, then to their
    -- folded name, so a picture with no filename to parse still groups sensibly.
    COALESCE(mmsi, imo, vessel_normalize(name)) AS group_key
FROM typed;

-- An IMO belongs to exactly one hull, so one that turns up under two different MMSIs
-- is evidence of a bad photo match, not of a ship with two numbers: Google Images
-- answered a search for Mystar's IMO 9892690 with a photo of Megastar, filing it under
-- Megastar's MMSI. Such a number is dropped from both groups rather than believed in
-- one of them; what is left is a number seen under a single MMSI, which is then the
-- group's IMO provided the group has only one such candidate.
CREATE TEMP TABLE vessel_imo ON COMMIT DROP AS
WITH candidates AS (
    SELECT DISTINCT group_key, imo FROM vessel_import WHERE imo IS NOT NULL
),
unambiguous AS (
    SELECT imo FROM candidates GROUP BY imo HAVING count(*) = 1
)
SELECT c.group_key, min(c.imo) AS imo
FROM candidates c
JOIN unambiguous u ON u.imo = c.imo
GROUP BY c.group_key
HAVING count(*) = 1;

-- Per group, the row that becomes the vessel.
CREATE TEMP TABLE vessel_group ON COMMIT DROP AS
SELECT
    group_key,
    max(mmsi) AS mmsi,
    (SELECT vi.imo FROM vessel_imo vi WHERE vi.group_key = g.group_key) AS imo,
    (
        SELECT i.name
        FROM vessel_import i
        WHERE i.group_key = g.group_key AND i.name IS NOT NULL
        GROUP BY i.name
        -- Most-used spelling wins; then the one that is not shouted, since the mixed
        -- case is the written name and the upper case is a search box ('Elbphilharmonie'
        -- over 'DE ELBPHILHARMONIE'); then the shortest, which drops the leftover
        -- prefixes and qualifiers ('Megastar' over 'M/S Megastar').
        ORDER BY count(*) DESC,
                 (i.name ~ '[[:lower:]]') DESC,
                 length(i.name),
                 i.name
        LIMIT 1
    ) AS name,
    (
        SELECT i.country_code
        FROM vessel_import i
        WHERE i.group_key = g.group_key AND i.country_code IS NOT NULL
        ORDER BY i.fetch_date DESC NULLS LAST, i.uid DESC
        LIMIT 1
    ) AS country_code
FROM vessel_import g
WHERE group_key IS NOT NULL
GROUP BY group_key;

-- MMSI groups first: they are the ones that merge spellings, and they own the number
-- the later groups would otherwise duplicate.
INSERT INTO vessels (name, imo, mmsi, country_code)
SELECT name, imo, mmsi, country_code FROM vessel_group WHERE mmsi IS NOT NULL;

-- Then the IMO-only groups, unless that IMO already arrived above.
INSERT INTO vessels (name, imo, mmsi, country_code)
SELECT g.name, g.imo, NULL, g.country_code
FROM vessel_group g
WHERE g.mmsi IS NULL
  AND g.imo IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM vessels v WHERE v.imo = g.imo);

-- Then the name-only groups, unless a vessel of that name already exists — those are
-- the same ship reached without a number, not a second one.
INSERT INTO vessels (name, imo, mmsi, country_code)
SELECT g.name, NULL, NULL, g.country_code
FROM vessel_group g
WHERE g.mmsi IS NULL
  AND g.imo IS NULL
  AND g.name IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM vessels v WHERE v.name_key = vessel_normalize(g.name));

-- Hang every cached photo off its vessel, by the strongest identifier it has.
UPDATE ship_pictures sp
SET vessel_id = v.uid
FROM vessel_import i
JOIN vessels v
  ON v.mmsi = i.mmsi
  OR (i.mmsi IS NULL AND v.imo = i.imo)
  OR (i.mmsi IS NULL AND i.imo IS NULL AND v.name_key = vessel_normalize(i.name))
WHERE sp.uid = i.uid;
