ALTER TABLE trips ADD COLUMN departure_delay INT DEFAULT NULL;
ALTER TABLE trips ADD COLUMN arrival_delay INT DEFAULT NULL;
-- unit: seconds (positive = late, negative = early, NULL = not recorded)
