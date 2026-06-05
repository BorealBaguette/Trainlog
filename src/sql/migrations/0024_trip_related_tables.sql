-- Trip-adjacent tables migrated from SQLite main.db, plus a PG equivalent of the
-- Python `remove_diacritics` SQLite function used by the trip-search queries.

-- Diacritic-insensitive search helper. Marked IMMUTABLE so it can be indexed and
-- used freely in WHERE clauses; mirrors py/utils.remove_diacritics for search.
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE OR REPLACE FUNCTION remove_diacritics(text)
RETURNS text AS $$
    SELECT unaccent('unaccent', COALESCE($1, ''))
$$ LANGUAGE sql IMMUTABLE;

-- Ticket uid and tag uid are referenced by trips.ticket_id and
-- tags_associations.tag_id respectively, so their ids are preserved on sync.
CREATE TABLE tickets (
    uid SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT NOT NULL,
    price FLOAT NOT NULL,
    currency TEXT NOT NULL,
    purchasing_date TIMESTAMP NOT NULL,
    notes TEXT,
    active BOOLEAN DEFAULT TRUE,
    active_countries TEXT
);
CREATE INDEX tickets_username_idx ON tickets (username);

CREATE TABLE tags (
    uid SERIAL PRIMARY KEY,
    uuid TEXT,
    username TEXT,
    name TEXT NOT NULL,
    colour VARCHAR(7),
    type TEXT DEFAULT 'voyage'
);
CREATE INDEX tags_username_idx ON tags (username);

CREATE TABLE tags_associations (
    tag_id INTEGER NOT NULL,
    trip_id INTEGER NOT NULL,
    PRIMARY KEY (tag_id, trip_id)
);
CREATE INDEX tags_associations_trip_id_idx ON tags_associations (trip_id);

CREATE TABLE gpx (
    uid SERIAL PRIMARY KEY,
    source TEXT,
    username TEXT,
    origin TEXT,
    destination TEXT,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration INTEGER,
    distance INTEGER,
    path TEXT,
    notes TEXT
);
CREATE INDEX gpx_username_idx ON gpx (username);

CREATE TABLE ship_pictures (
    uid SERIAL PRIMARY KEY,
    vessel_name TEXT NOT NULL,
    image_url TEXT,
    referrer_url TEXT,
    local_image_path TEXT,
    country_code TEXT,
    fetch_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
