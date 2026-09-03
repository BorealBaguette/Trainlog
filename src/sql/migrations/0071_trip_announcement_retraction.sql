-- A trip that stops being public, or is deleted, has its Discord post taken
-- down again (retract_announcement in src/trip_announcer.py). The row stays so
-- the trip is never announced a second time; `retracted` records that the post
-- is gone.
ALTER TABLE trip_announcements ADD COLUMN retracted TIMESTAMP;
