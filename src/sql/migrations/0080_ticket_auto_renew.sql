-- A subscription renewed at the same price on the same day of the month (a Navigo): once the
-- ticket has expired, its successors are created up to today, and the flag passes to the newest.
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS auto_renew BOOLEAN NOT NULL DEFAULT FALSE;
