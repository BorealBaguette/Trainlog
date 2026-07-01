-- Record how a trip's route was produced, so the editor can reopen it in the right mode
-- and we can tell hand-drawn / imported routes apart from router output.
-- Known values: 'router' (default), 'freehand', 'gpx', 'gpx_routed' (GPX snapped to the
-- network), 'fr24' (FlightRadar24 import). The user's freehand waypoints are kept in the
-- existing trips.waypoints column so the canvas can be restored exactly.
ALTER TABLE trips ADD COLUMN IF NOT EXISTS route_source TEXT DEFAULT 'router';
