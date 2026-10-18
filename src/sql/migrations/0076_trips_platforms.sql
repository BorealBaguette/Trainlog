-- The platform a trip left from and arrived at, as it actually was (the live one when
-- the timetable had it changed). Filled from a timetable run picked on the trip form
-- (MOTIS), or by hand in the edit page's stops dialog; NULL otherwise. Intermediate stops
-- keep theirs in the waypoints JSON (stop.platform / platform_rt), which the origin and
-- destination are not part of.
ALTER TABLE trips ADD COLUMN IF NOT EXISTS departure_platform TEXT;
ALTER TABLE trips ADD COLUMN IF NOT EXISTS arrival_platform TEXT;
