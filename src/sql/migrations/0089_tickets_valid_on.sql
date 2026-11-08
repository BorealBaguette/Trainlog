-- The day a single ticket is for, when it is not the day it was bought: tickets are sorted
-- and put in their year by it. It stays on its manual active switch; passes and cards have
-- their own dates (valid_from / valid_until) and leave it NULL.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS valid_on DATE;
