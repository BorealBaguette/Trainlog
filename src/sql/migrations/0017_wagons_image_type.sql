-- Add image_type column to wagons (plain / sides / sides_L / sides_R).
-- Truncate so load_base_data reloads from the updated CSV on next boot.
-- Trainsets are NOT truncated: they reference wagons by name (unchanged).
ALTER TABLE wagons ADD COLUMN image_type TEXT;
TRUNCATE wagons;
