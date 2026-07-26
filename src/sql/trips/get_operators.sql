-- The operators this user has used before, for the trip-form autocomplete.
--
-- Read from trip_operators rather than `trips.operator`, which fixes two things:
--
--   * multi-operator trips are stored as one comma-separated string, so selecting
--     DISTINCT operator offered "SNCF, TransN, ADP" as a single suggestion;
--   * a spelling the user has used is reported under its canonical name, so their own
--     "cff" no longer appears next to the known "CFF" as if they were two operators.
--
-- Spellings that match no operator are returned as typed — they are still the user's
-- own vocabulary, and dropping them would make previously used names unofferable.
SELECT DISTINCT COALESCE(o.short_name, tv.raw_name) AS operator
FROM trip_operators tv
LEFT JOIN operators o ON o.operator_id = tv.operator_id
WHERE tv.user_id = :user_id
