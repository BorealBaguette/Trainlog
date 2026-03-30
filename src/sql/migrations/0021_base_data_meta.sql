-- Track which CSV files have been loaded and when, so load_base_data can
-- detect a newer CSV and upsert only the new rows rather than skipping.
CREATE TABLE IF NOT EXISTS meta.base_data (
    table_name TEXT      PRIMARY KEY,
    csv_mtime  DOUBLE PRECISION NOT NULL,  -- Unix timestamp (os.path.getmtime)
    loaded_at  TIMESTAMPTZ DEFAULT NOW()
);
