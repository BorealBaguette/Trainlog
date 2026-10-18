-- A ticket valid over a period (a day ticket, a monthly pass) is active only within it; one
-- without stays on its manual switch. Its operator groups its renewals, and trips run by it or
-- by one of the secondary operators are those it may cover.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS valid_from DATE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS valid_until DATE;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS operator TEXT;
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS secondary_operators TEXT;
