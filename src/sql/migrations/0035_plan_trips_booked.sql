-- Mark whether a plan-trip's price is a real booked ticket or just a budget estimate.
-- false = estimate (purchase_date is only an FX reference); true = actually booked
-- (purchase_date is the real date paid).
ALTER TABLE plan_trips ADD COLUMN IF NOT EXISTS booked BOOLEAN DEFAULT FALSE;
