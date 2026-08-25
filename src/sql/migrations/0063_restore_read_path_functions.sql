-- Re-create two functions 0060 defines that databases which already ran it never got.
--
-- 0060 gained the columns form of station_display_name() and station_label_key() after it had
-- been applied, and a recorded migration never runs again — so an existing database is missing
-- both while a fresh one looks fine. The stations and routes stats fail outright without them.
--
-- Copied from 0060 rather than moved, so that file still reads as a whole. Both are
-- CREATE OR REPLACE, so this is a no-op where they already exist.

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
