-- How long one use of a clipping card lasts: every trip departing within that many minutes of
-- the one that opened the use rides on it (a TBM ticket allows connections for an hour).
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS use_minutes INTEGER;
