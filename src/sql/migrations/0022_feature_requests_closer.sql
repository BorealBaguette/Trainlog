-- Track who closed a feature request and when, so the UI can show
-- "Closed by X on YYYY-MM-DD HH:MM".
ALTER TABLE feature_requests
    ADD COLUMN IF NOT EXISTS closed_by TEXT,
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;
