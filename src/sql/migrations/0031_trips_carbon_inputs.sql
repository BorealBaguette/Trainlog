-- Persist the carbon inputs that were previously only used transiently (to
-- compute the countries elec/nonelec split and the carbon figure) then discarded,
-- so editing a trip reset them and the carbon backfill could not reproduce them:
--   * power_type    -- auto / electric / thermic / manual
--   * co2_override  -- ferry manual carbon override, g CO2 per passenger-km
-- Existing rows stay NULL (rail electric status is already baked into countries).
ALTER TABLE trips ADD COLUMN IF NOT EXISTS power_type TEXT;
ALTER TABLE trips ADD COLUMN IF NOT EXISTS co2_override DOUBLE PRECISION;
