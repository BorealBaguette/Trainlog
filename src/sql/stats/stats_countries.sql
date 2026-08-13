-- One row per trip; the per-country split happens in Python (get_stats_countries).
--
-- Doing the split here with jsonb_each was tried and measured slower: parsing the
-- TEXT column into jsonb once per row costs more than shipping the raw text and
-- letting Python's C JSON parser handle it (0.32-0.41s vs 0.08s for a 9k-trip
-- user; a wash on the all-users admin view). Keep the aggregation in Python.
{base_filter}
{time_categories}

SELECT
    countries,
    trip_length,
    trip_duration,
    carbon,
    arrival_delay,
    is_past AS "past",
    is_planned_future AS "plannedFuture"
FROM time_categories
WHERE is_project IS FALSE
