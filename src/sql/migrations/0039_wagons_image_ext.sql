-- Record each wagon image's file format so the renderer can build the correct URL
-- (.gif or .png) instead of assuming GIF. All existing artwork is GIF, so the default
-- keeps every current row valid with no backfill; PNG uploads set this to 'png' at write
-- time. Applies to both sides (image_L / image_R share one extension).
ALTER TABLE wagons ADD COLUMN IF NOT EXISTS image_ext TEXT NOT NULL DEFAULT 'gif';
