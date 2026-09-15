-- Share links for arbitrary trip selections.
--
-- Selecting a few hundred trips used to spell every id in the URL path (30 kB
-- for a big tag). The list is stored here once instead and the URL carries an
-- 8-character key: /public/trip/sK7f2Qw9, whatever the selection size.
--
-- ids_hash (sha256 of the sorted id list) makes the mapping one-to-one, so
-- sharing the same selection twice reuses the existing key instead of adding a
-- row. Rows are never expired: a shared link has to keep working.
CREATE TABLE trip_selections (
    key      TEXT PRIMARY KEY,
    trip_ids INTEGER[] NOT NULL,
    ids_hash TEXT NOT NULL UNIQUE,
    created  TIMESTAMP NOT NULL DEFAULT now()
);
