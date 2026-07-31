-- Canonical operators + aliases.
--
-- Until now `operators` was linked to trips by exact string equality on short_name,
-- so SBB / CFF / FFS were three unrelated operators and only the exact spelling a
-- user typed could pick up a logo. This migration introduces an alias table: many
-- spellings resolve to one operator. `trips.operator` stays free text and is still
-- what the user sees — aliases only drive logos, stats grouping and search.
--
-- Nothing regroups on its own: every existing short_name/long_name becomes an alias
-- of its own operator, so resolution right after this migration is equivalent to the
-- previous behaviour, merely case- and punctuation-insensitive. Real aliases are
-- added afterwards through the admin panel.


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Normalisation
-- ─────────────────────────────────────────────────────────────────────────────
-- Fold a written operator name to a comparison key: 'CFF', 'cff', 'C.F.F.' and
-- ' CFF ' all become 'cff'.
--
-- [:alnum:] is used rather than [a-z0-9] on purpose: the character class is
-- ctype-aware (the cluster runs en_US.utf8), so Cyrillic, Greek and CJK names are
-- preserved. A naive ASCII class would normalise ΣΤΑΣΥ, ЦППК, Московский Транспорт
-- and five other operators to the empty string and they could never resolve.
--
-- IMMUTABLE so it can back an index; that requires the two-argument form of
-- unaccent(), as the single-argument form is only STABLE. Same pattern as
-- remove_diacritics() in 0024.
CREATE OR REPLACE FUNCTION operator_normalize(text) RETURNS text AS $$
    SELECT NULLIF(
        lower(regexp_replace(unaccent('unaccent', COALESCE($1, '')), '[^[:alnum:]]+', '', 'g')),
        ''
    )
$$ LANGUAGE sql IMMUTABLE;

-- Which pool of operators a trip draws from. `operators.operator_type` has four
-- values; every transport trip type shares the 'operator' pool. Mirrors the mapping
-- already applied in getManAndOps (app.py) and keeps "Avis" the car-rental company
-- distinct from "Avis" the transport operator.
CREATE OR REPLACE FUNCTION operator_type_bucket(trip_type text) RETURNS text AS $$
    SELECT CASE WHEN $1 IN ('accommodation', 'car', 'poi') THEN $1 ELSE 'operator' END
$$ LANGUAGE sql IMMUTABLE;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Merge operators that differ only by case, accent or punctuation
-- ─────────────────────────────────────────────────────────────────────────────
-- e.g. 'MPK Poznań' x3, 'Cercanias'/'Cercanías', 'Rügensche BäderBahn'/'Rügensche
-- Bäderbahn'. These are duplicates of one real operator and are the reason the trip
-- queries needed LEFT JOIN LATERAL (... LIMIT 1) guards. Merging is scoped to one
-- operator_type so cross-pool namesakes (Avis, Ikea, Tomorrowland) are left alone.
--
-- The survivor is the row carrying the most logos (logos are the curated asset),
-- tie-broken by lowest operator_id. Losing spellings are not lost: step 4 records
-- every original short_name and long_name as an alias of the survivor.
CREATE TEMP TABLE operator_merge_map ON COMMIT DROP AS
WITH ranked AS (
    SELECT o.operator_id,
           o.operator_type,
           o.short_name,
           o.long_name,
           first_value(o.operator_id) OVER (
               PARTITION BY o.operator_type, operator_normalize(o.short_name)
               ORDER BY (SELECT count(*) FROM operator_logos l WHERE l.operator_id = o.operator_id) DESC,
                        o.operator_id
           ) AS keep_id
    FROM operators o
    WHERE operator_normalize(o.short_name) IS NOT NULL
)
SELECT operator_id, keep_id, operator_type, short_name, long_name FROM ranked;

-- Logos of the merged-away rows move to the survivor.
UPDATE operator_logos l
SET operator_id = m.keep_id
FROM operator_merge_map m
WHERE l.operator_id = m.operator_id
  AND m.operator_id <> m.keep_id;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Stable readable key
-- ─────────────────────────────────────────────────────────────────────────────
-- `slug` is the natural key used by the admin CSV round-trip, so operators can be
-- exported and re-imported without ever exposing numeric ids.
ALTER TABLE operators ADD COLUMN IF NOT EXISTS slug TEXT;

UPDATE operators o
SET slug = s.slug
FROM (
    SELECT operator_id,
           CASE WHEN rn = 1 THEN base ELSE base || '-' || rn END AS slug
    FROM (
        SELECT operator_id,
               COALESCE(operator_normalize(short_name), 'operator-' || operator_id) AS base,
               row_number() OVER (
                   PARTITION BY COALESCE(operator_normalize(short_name), 'operator-' || operator_id)
                   ORDER BY operator_id
               ) AS rn
        FROM operators
    ) numbered
) s
WHERE o.operator_id = s.operator_id;

ALTER TABLE operators ALTER COLUMN slug SET NOT NULL;
ALTER TABLE operators ADD CONSTRAINT operators_slug_key UNIQUE (slug);

-- Lets operator_aliases carry operator_type with a foreign key instead of an
-- application-maintained copy: changing an operator's type then cascades into its
-- aliases automatically (see the FK in step 4).
ALTER TABLE operators ADD CONSTRAINT operators_id_type_key UNIQUE (operator_id, operator_type);


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Aliases
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE operator_aliases (
    alias_id      SERIAL PRIMARY KEY,
    operator_id   INTEGER NOT NULL,
    -- Denormalised from operators so uniqueness can be scoped per pool. Kept honest
    -- by the composite FK below rather than by application code.
    operator_type VARCHAR(100) NOT NULL,
    -- The spelling as written, e.g. 'CFF'. Shown in the admin panel and offered in
    -- the autocomplete; never overwrites what a user typed on a trip.
    alias         TEXT NOT NULL,
    normalized    TEXT GENERATED ALWAYS AS (operator_normalize(alias)) STORED,
    -- Optional: 'fr' for CFF, 'it' for FFS. Unused for now, recorded for later.
    lang          TEXT,
    -- 'short' and 'long' mirror operators.short_name / long_name; 'alias' is an
    -- extra spelling added by an admin.
    kind          TEXT NOT NULL DEFAULT 'alias',
    CONSTRAINT operator_aliases_kind_check CHECK (kind IN ('short', 'long', 'alias')),
    -- A name that normalises to nothing would match everything; reject it.
    CONSTRAINT operator_aliases_normalized_check CHECK (normalized IS NOT NULL),
    FOREIGN KEY (operator_id, operator_type)
        REFERENCES operators (operator_id, operator_type)
        ON UPDATE CASCADE ON DELETE CASCADE
);

-- One meaning per spelling, per pool. Scoped by operator_type because 44 names are
-- shared across pools ('Avis' is both a car-rental company and a bus operator).
CREATE UNIQUE INDEX operator_aliases_type_normalized_key
    ON operator_aliases (operator_type, normalized);
CREATE INDEX operator_aliases_operator_id_idx ON operator_aliases (operator_id);
-- Resolution prefers the trip's own pool but falls back to any pool when that pool
-- has no match (see insert_trip_operators.sql), so lookups on normalized alone must
-- be indexed too — the unique index above is not usable for them.
CREATE INDEX operator_aliases_normalized_idx ON operator_aliases (normalized);

-- Short names first so that, where a long_name of one operator collides with the
-- short_name of another (96 such pairs), the short name wins — it is what users
-- actually type. Surviving operators before merged-away ones, for the same reason.
INSERT INTO operator_aliases (operator_id, operator_type, alias, kind)
SELECT m.keep_id, m.operator_type, m.short_name, 'short'
FROM operator_merge_map m
ORDER BY (m.operator_id <> m.keep_id), m.operator_id
ON CONFLICT (operator_type, normalized) DO NOTHING;

INSERT INTO operator_aliases (operator_id, operator_type, alias, kind)
SELECT m.keep_id, m.operator_type, m.long_name, 'long'
FROM operator_merge_map m
WHERE operator_normalize(m.long_name) IS NOT NULL
ORDER BY (m.operator_id <> m.keep_id), m.operator_id
ON CONFLICT (operator_type, normalized) DO NOTHING;

-- Operators whose short_name normalises to nothing were excluded from the merge map
-- and so have no alias yet. There should be none (every current short_name contains
-- at least one alphanumeric character) but the long_name is worth keeping if so.
INSERT INTO operator_aliases (operator_id, operator_type, alias, kind)
SELECT o.operator_id, o.operator_type, o.long_name, 'long'
FROM operators o
WHERE operator_normalize(o.short_name) IS NULL
  AND operator_normalize(o.long_name) IS NOT NULL
ON CONFLICT (operator_type, normalized) DO NOTHING;


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Drop the merged-away operators
-- ─────────────────────────────────────────────────────────────────────────────
-- Their logos and names have been transferred above.
DELETE FROM operators o
USING operator_merge_map m
WHERE o.operator_id = m.operator_id
  AND m.operator_id <> m.keep_id;
