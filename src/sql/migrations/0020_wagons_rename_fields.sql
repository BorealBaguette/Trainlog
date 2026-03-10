-- Rename French-named wagons columns to meaningful English names.
-- Add author, license (MLG is CC BY-NC-SA 3.0), and gauge (mm, int) columns.
-- Truncate so load_base_data reloads from the updated CSV on next boot.
-- Trainsets are NOT truncated: they reference wagons by name (unchanged).
-- All DDL statements are idempotent (safe to rerun if migration record was deleted).

DO $$
BEGIN
    -- Column renames (skip if old name no longer exists)
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='wagons' AND column_name='titre1') THEN
        ALTER TABLE wagons RENAME COLUMN titre1    TO category;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='wagons' AND column_name='titre2') THEN
        ALTER TABLE wagons RENAME COLUMN titre2    TO subcategory;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='wagons' AND column_name='nom') THEN
        ALTER TABLE wagons RENAME COLUMN nom       TO label;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='wagons' AND column_name='epo') THEN
        ALTER TABLE wagons RENAME COLUMN epo       TO era;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='wagons' AND column_name='datmaj') THEN
        ALTER TABLE wagons RENAME COLUMN datmaj    TO updated_on;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='wagons' AND column_name='typeligne') THEN
        ALTER TABLE wagons RENAME COLUMN typeligne TO line_type;
    END IF;

    -- New columns (skip if already present)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='wagons' AND column_name='author') THEN
        ALTER TABLE wagons ADD COLUMN author  TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='wagons' AND column_name='license') THEN
        ALTER TABLE wagons ADD COLUMN license TEXT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='wagons' AND column_name='gauge') THEN
        ALTER TABLE wagons ADD COLUMN gauge   INT;
    END IF;
END $$;

-- Drop old trigram indexes (IF EXISTS makes these safe to rerun)
DROP INDEX IF EXISTS idx_wagons_nom_trgm;
DROP INDEX IF EXISTS idx_wagons_titre1_trgm;
DROP INDEX IF EXISTS idx_wagons_titre2_trgm;
DROP INDEX IF EXISTS idx_wagons_notes_trgm;

-- Recreate with new names (IF NOT EXISTS makes these safe to rerun)
CREATE INDEX IF NOT EXISTS idx_wagons_label_trgm       ON wagons USING gin (label       gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_wagons_category_trgm    ON wagons USING gin (category    gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_wagons_subcategory_trgm ON wagons USING gin (subcategory gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_wagons_notes_trgm       ON wagons USING gin (notes       gin_trgm_ops);

TRUNCATE wagons;

-- Rename 'nom' -> 'label' in stored inline JSON compositions (trips.material_type_advanced).
-- Rows that contain a named trainset (plain string) or NULL are left untouched.
DO $$
DECLARE
    r           RECORD;
    updated_json TEXT;
BEGIN
    FOR r IN
        SELECT trip_id, material_type_advanced
        FROM   trips
        WHERE  material_type_advanced IS NOT NULL
          AND  material_type_advanced LIKE '[%'
    LOOP
        BEGIN
            SELECT jsonb_agg(
                       CASE
                           WHEN unit ? 'nom'
                           THEN (unit - 'nom') || jsonb_build_object('label', unit->>'nom')
                           ELSE unit
                       END
                   )::text
            INTO   updated_json
            FROM   jsonb_array_elements(r.material_type_advanced::jsonb) AS unit;

            IF updated_json IS NOT NULL THEN
                UPDATE trips
                SET    material_type_advanced = updated_json
                WHERE  trip_id = r.trip_id;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Skipping trip % (invalid JSON): %', r.trip_id, SQLERRM;
        END;
    END LOOP;
END $$;
