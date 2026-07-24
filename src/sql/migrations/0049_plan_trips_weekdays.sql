-- Weekday constraint for relative ("Day N") plan legs, to track transit with
-- less-than-daily frequencies. Bitmask: bit 0 = Monday ... bit 6 = Sunday.
-- NULL = no constraint (runs daily / not specified).
ALTER TABLE plan_trips ADD COLUMN weekdays INTEGER;
