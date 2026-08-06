-- Where a cached ship photo came from, and under what licence.
--
-- Every photo so far arrived by one route — a Google Images search that only ever
-- matched vesselfinder — so the credit could be hard-coded: the trip pages print
-- "©Vesselfinder.com" under whatever they are showing. That stops being true the moment
-- a second source exists, and admins can already upload their own.
--
-- Wikimedia Commons is the second source: freely licensed, but the licences are only
-- free if the attribution travels with the file. CC BY-SA needs the author's name and
-- the licence named; a photo shown without them is a licence breach, not a formatting
-- detail. So the row carries its own provenance and the display reads it, instead of
-- every caller assuming.
ALTER TABLE ship_pictures
    ADD COLUMN source  TEXT,   -- 'vesselfinder' | 'wikimedia' | 'upload'
    ADD COLUMN author  TEXT,   -- who took it, where the licence requires crediting them
    ADD COLUMN license TEXT;   -- the licence's short name, e.g. 'CC BY-SA 4.0'

-- The existing rows, in the two ways they were made. A row with a referrer is a search
-- result — always vesselfinder, since that is what the custom search engine returns;
-- one with a file and no remote url at all was uploaded through the admin panel.
UPDATE ship_pictures
SET source = CASE
        WHEN referrer_url IS NOT NULL OR image_url IS NOT NULL THEN 'vesselfinder'
        WHEN local_image_path IS NOT NULL THEN 'upload'
    END;

-- Answering "which photos may I show, and how must I credit them" without reading every
-- row.
CREATE INDEX ship_pictures_source_idx ON ship_pictures (source);
