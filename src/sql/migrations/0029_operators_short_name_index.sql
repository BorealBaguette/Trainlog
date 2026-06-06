-- Index operators by short_name. The trip queries join operators on short_name
-- (get_trip.sql, get_dynamic_user_trips.sql). Those joins use LEFT JOIN LATERAL
-- (... LIMIT 1) because short_name is not unique (e.g. two "SNCB" rows would
-- otherwise duplicate every Belgian trip). Without this index the lateral lookup
-- sequentially scans the whole operators table once per trip row, which is slow
-- for users with many trips; with it each lookup is an index scan.
CREATE INDEX IF NOT EXISTS idx_operators_short_name ON operators (short_name);
