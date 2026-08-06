-- Widen the ship-prefix list vessel_normalize() folds away.
--
-- 0054 stripped a handful of prefixes so that 'M/S Megastar' and 'Megastar' resolved to
-- one ship. The list was short, and the ones it missed are the ones people actually
-- type: 'MS Fjordtroll', 'MV Fjordtroll' and 'Fjordtroll' still went their separate
-- ways, and neither the autocomplete nor trip search could bring them back together.
--
-- The prefix is a title, not part of the name — 'MS' says motor ship the way 'HMS' says
-- His Majesty's Ship — so folding it away is what makes the three spellings one search.
-- A separator after the prefix stays mandatory, which is what keeps 'Mystar', 'Msida'
-- and 'Psyche' intact.
--
-- Deliberately NOT in the list:
--   * bare 'my'  — motor yacht, but also the start of any number of real names
--                  ('My Dream'); 'm/y' with its slash is unambiguous and is included.
--   * 'de', 'da', 'el', 'le' and other articles that look like two-letter prefixes:
--     'De Zijlboot' and 'De Vriendschap' are the ship's whole name.
--   * 'msc' — that is a shipping line, and 'MSC Meraviglia' is the vessel's real name.
CREATE OR REPLACE FUNCTION vessel_normalize(text) RETURNS text AS $$
    SELECT NULLIF(
        regexp_replace(
            lower(
                regexp_replace(
                    unaccent('unaccent', COALESCE($1, '')),
                    -- Motor, steam, diesel, turbine and sail; then the naval and royal
                    -- prefixes; then the high-speed and service ones. Ordered longest
                    -- first within each family so 'm/s' cannot match before 'm/sv'-like
                    -- additions later, and alternation stays predictable.
                    '^\s*('
                    'm/?s|m/?v|m/?f|m/?t|m/?y|s/?s|d/?s|t/?s|f/?b|r/?v|s/?y|'
                    'hmnzs|hmcs|hmas|hms|rms|usns|uss|'
                    'hsc|hss|hsv|gts|tss|ps|fv|nb'
                    ')[\s./_-]+',
                    '',
                    'i'
                )
            ),
            '[^[:alnum:]]+', '', 'g'
        ),
        ''
    )
$$ LANGUAGE sql IMMUTABLE;

-- `vessels.name_key` is a STORED generated column over this function, and PostgreSQL
-- does not recompute stored values when the function behind them is replaced — the
-- existing rows would keep the keys 0054 computed, so 'HSC Judita' would still be
-- findable and 'MS Fjordtroll' still would not. A no-op UPDATE rewrites every row and
-- recomputes the column (and its index) with the new definition.
UPDATE vessels SET name = name WHERE name IS NOT NULL;
