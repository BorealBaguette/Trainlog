-- Wagon catalogue (populated from base_data/wagons.csv on first boot)
CREATE TABLE wagons (
    id        SERIAL PRIMARY KEY,
    source    TEXT,
    titre1    TEXT,
    titre2    TEXT,
    nom       TEXT,
    epo       TEXT,
    datmaj    TEXT,
    image     TEXT,
    notes     TEXT,
    typeligne TEXT
);

-- Fast image-path lookups (used by the trainset builder when loading a set)
CREATE INDEX idx_wagons_image ON wagons (image);

-- Trigram indexes for the ILIKE search (requires pg_trgm, added in migration 0013)
CREATE INDEX idx_wagons_nom_trgm    ON wagons USING gin (nom    gin_trgm_ops);
CREATE INDEX idx_wagons_titre1_trgm ON wagons USING gin (titre1 gin_trgm_ops);
CREATE INDEX idx_wagons_titre2_trgm ON wagons USING gin (titre2 gin_trgm_ops);
CREATE INDEX idx_wagons_notes_trgm  ON wagons USING gin (notes  gin_trgm_ops);

-- Saved trainsets: only IDs + flip-side stored per unit, wagon details joined on read
CREATE TABLE trainsets (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    username   TEXT,
    is_admin   BOOLEAN NOT NULL DEFAULT FALSE,
    units_json TEXT    NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_trainsets_username ON trainsets (username);
