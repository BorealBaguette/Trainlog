-- An admin-made version of the logo for the dark theme, shown instead of the one
-- dark_logos.js derives. Set on every row sharing the logo file.
ALTER TABLE operator_logos ADD COLUMN IF NOT EXISTS dark_logo_url TEXT;
