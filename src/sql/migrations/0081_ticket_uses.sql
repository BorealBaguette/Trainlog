-- A clipping card: a ticket for a number of trips rather than, or as well as, a period. Used up,
-- it is no longer active.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS uses INTEGER;
