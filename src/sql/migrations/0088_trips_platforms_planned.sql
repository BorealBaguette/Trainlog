-- The platform a trip was scheduled to leave from / arrive at, when an actual one was
-- given apart from it (departure_platform / arrival_platform stay the one it actually
-- used). NULL: no separate actual one known, the platform columns are all there is.
-- Set in the edit page's stops dialog, like stop.platform / platform_rt on the stops.
ALTER TABLE trips ADD COLUMN IF NOT EXISTS departure_platform_planned TEXT;
ALTER TABLE trips ADD COLUMN IF NOT EXISTS arrival_platform_planned TEXT;
