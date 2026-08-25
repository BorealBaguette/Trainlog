-- What the seeding script decided about a label, so the queue can say why it is still there.
--
-- A refusal used to leave no trace, so a spelling nothing had looked at and one the script
-- examined and could not settle looked identical — and only the second needs a human.
ALTER TABLE station_labels
    -- NULL until checked. See find_candidate() in scripts/seed_stations.py for the verdicts.
    ADD COLUMN IF NOT EXISTS auto_checked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS auto_result TEXT;

-- The queue filters on this, over the unresolved rows only.
CREATE INDEX IF NOT EXISTS station_labels_auto_result_idx
    ON station_labels (auto_result)
    WHERE station_id IS NULL;
