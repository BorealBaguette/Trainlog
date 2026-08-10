-- Recurring "commute" routes: a user-named, ordered sequence of legs that
-- recur together (e.g. a daily train+metro+metro commute, or an open chain
-- like home->work->gym->home). No "direction" or "round trip" concept is
-- stored -- whether a commute loops back to its start is derived by the app
-- from comparing the first and last leg's stations, never hard-coded here,
-- so both round-trip and non-round-trip chains are represented identically.
CREATE TABLE commute_routes (
    uid SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    archived BOOLEAN NOT NULL DEFAULT FALSE,
    created TIMESTAMP NOT NULL DEFAULT now(),
    last_modified TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX commute_routes_user_id_idx ON commute_routes (user_id);

-- One row per day's run of the commute. Groups the trips created together so
-- they can be counted/collapsed, and identifies the template: the FIRST
-- occurrence (MIN(uid) for a commute) is the stable reference that later
-- "log occurrence" calls clone route details and scheduled leg times from --
-- never the latest, so a delayed day never drifts the template.
CREATE TABLE commute_occurrences (
    uid SERIAL PRIMARY KEY,
    commute_route_id INTEGER NOT NULL REFERENCES commute_routes(uid) ON DELETE CASCADE,
    created TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX commute_occurrences_route_idx ON commute_occurrences (commute_route_id);

CREATE TABLE commute_route_trips (
    occurrence_id INTEGER NOT NULL REFERENCES commute_occurrences(uid) ON DELETE CASCADE,
    trip_id INTEGER NOT NULL UNIQUE REFERENCES trips(trip_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    PRIMARY KEY (occurrence_id, trip_id),
    UNIQUE (occurrence_id, sequence)
);
CREATE INDEX commute_route_trips_occurrence_idx ON commute_route_trips (occurrence_id);
