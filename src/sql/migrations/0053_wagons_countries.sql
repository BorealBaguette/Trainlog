-- Countries a wagon type normally operates in, so the catalogue can say where a
-- given vehicle is actually seen. Comma-separated ISO 3166-1 alpha-2 codes,
-- following the tickets.active_countries convention rather than introducing an
-- array type for one column.
ALTER TABLE wagons ADD COLUMN IF NOT EXISTS countries TEXT;
