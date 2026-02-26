-- Replace unstable SERIAL id with a stable name key (basename of the image path).
-- Truncate so load_base_data will reload from the updated CSV.
TRUNCATE wagons CASCADE;
TRUNCATE trainsets CASCADE;

ALTER TABLE wagons DROP COLUMN id;
ALTER TABLE wagons ADD COLUMN name TEXT NOT NULL;
ALTER TABLE wagons ADD CONSTRAINT wagons_pkey PRIMARY KEY (name);
