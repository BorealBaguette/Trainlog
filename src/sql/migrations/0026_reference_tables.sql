-- Reference / geo tables migrated from SQLite main.db.

-- Airport reference data (lookup by ident/iata). No natural PK in the source.
CREATE TABLE airports (
    iata TEXT,
    ident TEXT,
    name TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    iso_country TEXT,
    city TEXT
);
CREATE INDEX airports_ident_idx ON airports (ident);
CREATE INDEX airports_iata_idx ON airports (iata);

-- Train station reference data (lookup by id, fuzzy search on processed_name).
CREATE TABLE train_stations (
    id INTEGER PRIMARY KEY,
    name TEXT,
    latin_name TEXT,
    city TEXT,
    latin_city TEXT,
    country_code TEXT,
    longitude DOUBLE PRECISION,
    latitude DOUBLE PRECISION,
    processed_name TEXT
);
CREATE INDEX train_stations_processed_name_trgm
    ON train_stations USING gin (processed_name gin_trgm_ops);
CREATE INDEX train_stations_country_idx ON train_stations (country_code);

-- User-created manual stations.
CREATE TABLE manual_stations (
    uid SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    creator TEXT,
    station_type TEXT NOT NULL
);
CREATE INDEX manual_stations_station_type_idx ON manual_stations (station_type);

-- HERE API operator name mapping.
CREATE TABLE here_api_operators (
    here_operator TEXT PRIMARY KEY,
    trainlog_operator TEXT
);
