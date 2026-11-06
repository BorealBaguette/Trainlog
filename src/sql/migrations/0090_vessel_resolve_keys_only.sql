-- A trip's `reg` names a hull only through its key: the IMO, or the trainlog_id where
-- there is none. Names and MMSIs are not unique — 'MS Friesland' and 'Friesland' fold
-- to the same key, and an MMSI is reissued — so text that merely matches one stays the
-- text it was typed as. The ferry form stores a key only when a ship is picked.
CREATE OR REPLACE FUNCTION vessel_resolve(text) RETURNS integer AS $$
    SELECT q.vessel_id
    FROM (
        SELECT v.uid AS vessel_id, 1 AS rank
        FROM vessels v WHERE v.imo = btrim($1)
        UNION ALL
        SELECT v.uid, 2
        FROM vessels v WHERE v.trainlog_id = upper(btrim($1))
    ) q
    ORDER BY q.rank
    LIMIT 1
$$ LANGUAGE sql STABLE;
