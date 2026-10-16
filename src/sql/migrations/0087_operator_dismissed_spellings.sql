-- Unresolved operator spellings an admin has taken out of the work queue because they
-- will never match an operator ("unknown", a private boat). Keyed on the same
-- normalized form alias matching uses, so every variant of a spelling goes at once.
-- Trips are untouched; this only filters the queue.
CREATE TABLE operator_dismissed_spellings (
    dismissed_id SERIAL PRIMARY KEY,
    -- As it appeared in the queue when dismissed.
    spelling     TEXT NOT NULL,
    normalized   TEXT GENERATED ALWAYS AS (operator_normalize(spelling)) STORED
                 NOT NULL UNIQUE,
    reason       TEXT NOT NULL,
    dismissed_by TEXT NOT NULL,
    dismissed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A spelling that gains an alias did resolve after all, so its dismissal goes. Left
-- in place it would hide the spelling's trips again if the alias were later removed.
-- A trigger rather than app code, so every path adding an alias is covered.
CREATE FUNCTION operator_aliases_drop_dismissed() RETURNS trigger AS $$
BEGIN
    DELETE FROM operator_dismissed_spellings WHERE normalized = NEW.normalized;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER operator_aliases_drop_dismissed
    AFTER INSERT OR UPDATE OF alias ON operator_aliases
    FOR EACH ROW EXECUTE FUNCTION operator_aliases_drop_dismissed();
