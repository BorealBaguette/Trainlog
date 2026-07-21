-- Operator equivalence groups.
--
-- Aliases answer "these spellings are the same operator". Groups answer a different
-- question: "these *operators* are the same company". SBB, CFF and FFS are not
-- misspellings of one another — they are the company's three official names, and each
-- has its own logo. Folding them into one operator (a merge) would have to pick a
-- survivor, which makes every Geneva user's stats say SBB.
--
-- A group leaves all members intact and equal. It only says they count as one when
-- aggregating; the stats label each user sees is whichever member that user logs most
-- (see stats_operator.sql), so the same grouping reads as CFF in Geneva and SBB in
-- Zurich.
--
-- Grouping is purely a read-time concern: trip_operators still points at the specific
-- operator a trip named, so linking or unlinking operators requires no re-resolution
-- and a CFF trip keeps showing the CFF logo.
--
-- Merging remains available and is still the right tool for genuine duplicates —
-- rows like 'MPK Poznań' three times over, where there is no side to take.

CREATE TABLE operator_groups (
    group_id   SERIAL PRIMARY KEY,
    -- Free-text note for the admin ("Swiss Federal Railways: de/fr/it"). Never shown
    -- to users: the displayed name is always a real member's, never a label.
    note       TEXT,
    created_on TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE operators
    ADD COLUMN group_id INTEGER REFERENCES operator_groups (group_id) ON DELETE SET NULL;

-- Partial: only a small minority of operators will ever be grouped.
CREATE INDEX operators_group_id_idx ON operators (group_id) WHERE group_id IS NOT NULL;
