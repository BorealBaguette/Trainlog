-- One row per trip announced to Discord by the trip announcer.
--
-- The primary key is the claim: every worker polls, but the INSERT ... ON
-- CONFLICT DO NOTHING that precedes the API call only returns a row in the
-- worker that got there first, so a trip is posted exactly once however many
-- gunicorn workers (or restarts) are running.
CREATE TABLE trip_announcements (
    trip_id    INTEGER PRIMARY KEY REFERENCES trips(trip_id) ON DELETE CASCADE,
    announced  TIMESTAMP NOT NULL DEFAULT now(),
    message_id TEXT
);
