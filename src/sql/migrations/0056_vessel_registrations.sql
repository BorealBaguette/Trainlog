-- Split a ship into the hull and the identities it has carried.
--
-- 0054 made `vessels` one row per ship, holding its name, MMSI and flag together. That
-- cannot describe a ship that is sold: rename the row and every past trip silently
-- claims you sailed on the new name under the new ensign, which is not what happened.
-- Keep two rows and it cannot describe a hull either — one ship becomes several, and
-- its trips scatter.
--
-- So the two facts are separated:
--
--   vessels               the hull. Permanent. Identified by its IMO, which never
--                         changes, or by a synthetic id where there is no IMO.
--   vessel_registrations  the hull under one identity — an MMSI, with the name and
--                         flag that went with it, and the date it took effect.
--
-- `trips.reg` holds the hull key, and the name, flag and photo shown for a trip are
-- whichever registration was in force ON THE TRIP'S OWN DATE. A crossing in 2015 keeps
-- the 2015 name for good, while the register stays free to record what the ship is
-- called now. The trip's date is the authority, deliberately: someone logging an old
-- crossing today would otherwise pin it to today's MMSI, which was never on that ship.
--
-- IMO 8601915 is the shape of it: Amorella under the Finnish flag from 1988, Mega
-- Victoria under the Italian one from October 2022. A crossing logged in 2008 reads
-- Amorella.
--
-- The time rule is `operator_logos` (0014) exactly, including its lesson — a trip with
-- NO date takes the NEWEST registration, not the oldest. Getting that backwards is what
-- once put a 1937 SNCF logo on undated trips (see get_trip.sql).


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. The identity a hull carries for a period
-- ─────────────────────────────────────────────────────────────────────────────
-- No end date: the next registration's effective_date closes the previous one, so a
-- rename is one INSERT and periods cannot overlap or leave gaps. NULL means "from the
-- beginning", which is what every row gets in this migration.
CREATE TABLE vessel_registrations (
    uid            SERIAL PRIMARY KEY,
    vessel_id      INTEGER NOT NULL REFERENCES vessels(uid) ON DELETE CASCADE,
    mmsi           TEXT,
    name           TEXT,
    name_key       TEXT GENERATED ALWAYS AS (vessel_normalize(name)) STORED,
    country_code   TEXT,
    effective_date TIMESTAMP,
    updated_on     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT vessel_registrations_mmsi_format
        CHECK (mmsi IS NULL OR mmsi ~ '^[0-9]{9}$'),
    -- A registration with neither a number nor a name identifies nothing.
    CONSTRAINT vessel_registrations_identified
        CHECK (name_key IS NOT NULL OR mmsi IS NOT NULL)
);

-- The MMSI index is deliberately NOT unique. An MMSI is a flag-state allocation, not a
-- property of a hull: a ship renamed under the same flag keeps its number across two
-- periods, and numbers are returned and reissued to other ships entirely. Nothing that
-- reads it needs uniqueness — vessel_resolve() picks one row deterministically — and
-- what is worth preventing, an admin typing a number that belongs to a different hull,
-- is checked in the admin route where it can explain itself.
CREATE INDEX vessel_registrations_mmsi_idx
    ON vessel_registrations (mmsi) WHERE mmsi IS NOT NULL;
CREATE INDEX vessel_registrations_name_key_idx
    ON vessel_registrations (name_key) WHERE name_key IS NOT NULL;
-- The lookup vessel_identity() makes: a hull's periods, newest first.
CREATE INDEX vessel_registrations_period_idx
    ON vessel_registrations (vessel_id, effective_date DESC NULLS LAST, uid DESC);

-- Every existing row becomes its hull's one and only registration, undated. One period
-- that is always in force resolves exactly as 0054 did, so nothing observable changes
-- until somebody records a second one.
--
-- Except a row carrying only an IMO: we know the hull exists and nothing about what it
-- has been called. That is a hull with no registration yet — a state the old shape had
-- no way to express — and it keeps displaying whatever the trip typed until someone
-- fills one in.
INSERT INTO vessel_registrations (vessel_id, mmsi, name, country_code)
SELECT uid, mmsi, name, country_code
FROM vessels
WHERE mmsi IS NOT NULL OR name_key IS NOT NULL;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. `vessels` becomes the hull alone
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE vessels DROP CONSTRAINT vessels_identified;
ALTER TABLE vessels DROP CONSTRAINT vessels_mmsi_format;
-- Dropping these takes their indexes with them.
ALTER TABLE vessels DROP COLUMN name_key;
ALTER TABLE vessels DROP COLUMN name;
ALTER TABLE vessels DROP COLUMN mmsi;
ALTER TABLE vessels DROP COLUMN country_code;

-- Not every ship has an IMO — it is required for hulls over 100 tons on international
-- voyages, which leaves out most of the small ferries people actually log. Those need a
-- key of their own to sit in `trips.reg`, so every hull gets one: 'TL' and its uid,
-- which cannot be mistaken for a 7-digit IMO or a 9-digit MMSI.
--
-- It is meaningless to a human and must stay that way. vessel_resolve() matches it,
-- because that is what a trip may be holding; the autocomplete and trip search never
-- do, so it is never offered as something to type.
ALTER TABLE vessels
    ADD COLUMN trainlog_id TEXT GENERATED ALWAYS AS ('TL' || lpad(uid::text, 7, '0')) STORED;
CREATE UNIQUE INDEX vessels_trainlog_id_idx ON vessels (trainlog_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Resolution
-- ─────────────────────────────────────────────────────────────────────────────
-- Written text -> hull. Still accepts a name or an MMSI, because that is what is
-- painted on the ship and what users type; those route through a registration to the
-- hull that carried it. A name that two hulls have carried resolves to one of them
-- deterministically, as before.
--
-- Four separate index lookups UNIONed, rather than one OR-chain across the join of the
-- two tables. PostgreSQL cannot serve such an OR from an index and would materialise
-- and scan the whole join on every call — at one call per trip that took the periods
-- endpoint to 67 seconds and made the admin list, the stats grouping and the trips
-- listing pay the same toll. Each branch below hits an index; the rank column keeps the
-- precedence, so the hull's own keys outrank a registration's and an exact number
-- outranks a name.
CREATE OR REPLACE FUNCTION vessel_resolve(text) RETURNS integer AS $$
    SELECT q.vessel_id
    FROM (
        SELECT v.uid AS vessel_id, 1 AS rank
        FROM vessels v WHERE v.imo = btrim($1)
        UNION ALL
        SELECT v.uid, 2
        FROM vessels v WHERE v.trainlog_id = upper(btrim($1))
        UNION ALL
        SELECT r.vessel_id, 3
        FROM vessel_registrations r WHERE r.mmsi = btrim($1)
        UNION ALL
        SELECT r.vessel_id, 4
        FROM vessel_registrations r
        WHERE r.name_key IS NOT NULL AND r.name_key = vessel_normalize($1)
    ) q
    ORDER BY q.rank, q.vessel_id
    LIMIT 1
$$ LANGUAGE sql STABLE;

-- Which registration a hull was under at a given moment. A NULL moment means "now" —
-- an undated trip, or a caller that wants the current identity — and takes the newest,
-- never the oldest.
CREATE OR REPLACE FUNCTION vessel_identity(integer, timestamp) RETURNS integer AS $$
    SELECT r.uid
    FROM vessel_registrations r
    WHERE r.vessel_id = $1
      AND ($2 IS NULL OR r.effective_date IS NULL OR r.effective_date <= $2)
    ORDER BY r.effective_date DESC NULLS LAST, r.uid DESC
    LIMIT 1
$$ LANGUAGE sql STABLE;

-- What to show for a written identifier at a given moment: the ship's name as it was
-- then, or the text itself when we hold no record of it.
--
-- `fold_unknown` decides what happens to unrecognised text — displaying a trip leaves it
-- as typed (false), stats grouping upper-cases it (true) so case variants share a bar.
DROP FUNCTION IF EXISTS vessel_display_name(text, boolean);
CREATE OR REPLACE FUNCTION vessel_display_name(text, timestamp, boolean DEFAULT false)
RETURNS text AS $$
    SELECT COALESCE(
        (SELECT NULLIF(btrim(r.name), '')
         FROM vessel_registrations r
         WHERE r.uid = vessel_identity(vessel_resolve($1), $2)),
        CASE WHEN $3 THEN upper(btrim($1)) ELSE btrim($1) END
    )
$$ LANGUAGE sql STABLE;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Trips point at the hull
-- ─────────────────────────────────────────────────────────────────────────────
-- Only where the reg already resolves; anything naming no vessel we hold is left as
-- typed, and still displays as typed. This is the one place a user's own text is
-- overwritten, and what it is replaced by is a key that resolves to the same ship and
-- displays the same name — while making a later rename update every trip at once
-- instead of orphaning them.
UPDATE trips
SET reg = COALESCE(v.imo, v.trainlog_id)
FROM vessels v
WHERE trips.trip_type = 'ferry'
  AND trips.reg IS NOT NULL
  AND btrim(trips.reg) <> ''
  AND v.uid = vessel_resolve(trips.reg)
  AND btrim(trips.reg) <> COALESCE(v.imo, v.trainlog_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Photos belong to a registration
-- ─────────────────────────────────────────────────────────────────────────────
-- A photo shows a name on the hull and a flag on the funnel: it is a picture of the
-- ship as it was, not of the hull in the abstract.
ALTER TABLE ship_pictures
    ADD COLUMN registration_id INTEGER REFERENCES vessel_registrations(uid) ON DELETE CASCADE;

UPDATE ship_pictures sp
SET registration_id = r.uid
FROM vessel_registrations r
WHERE r.vessel_id = sp.vessel_id;

ALTER TABLE ship_pictures DROP COLUMN vessel_id;
CREATE INDEX ship_pictures_registration_idx ON ship_pictures (registration_id);
