-- Travel days of a rail pass (Interrail, Eurail, …) in a plan, to see how many days a
-- plan needs before choosing which pass to buy. Off until the plan turns it on.
--
-- A day is marked "required" (a travel day the plan can't do without) or "optional"
-- (one it could do on a separate ticket); unmarked days don't count. The day is the
-- one the plan shows: 'D<n>' for a Day N leg's day (so it moves with Day 1), or
-- 'A<YYYY-MM-DD>' for a dated leg's. A mark whose day no longer has a leg is kept
-- (the leg may come back) but not counted.
ALTER TABLE plans ADD COLUMN IF NOT EXISTS pass_days_enabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS plan_pass_days (
    plan_id INTEGER NOT NULL REFERENCES plans(uid) ON DELETE CASCADE,
    day_key TEXT NOT NULL CHECK (day_key ~ '^(D[0-9]{1,4}|A[0-9]{4}-[0-9]{2}-[0-9]{2})$'),
    status  TEXT NOT NULL CHECK (status IN ('required', 'optional')),
    PRIMARY KEY (plan_id, day_key)
);
