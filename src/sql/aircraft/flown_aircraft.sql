-- Aircraft type codes the given users have flown on, as logged (the airliners
-- table and the family rules are applied in src/api/aircraft.py).
--
-- Air trips keep the ICAO type code in material_type; a trip can list several
-- comma-separated codes (a change of aircraft on the same flight number), and
-- each counts. Only flights already taken: no projects, nothing in the future.
--
-- codes_only: one row per user and code, for the leaderboard. Otherwise one
-- row per trip and code, with what the aircraft page shows about it.
WITH flown AS (
    SELECT DISTINCT
        t.user_id,
        UPPER(TRIM(code)) AS code,
        t.trip_id,
        t.trip_length,
        COALESCE(t.utc_start_datetime, t.start_datetime)::date AS flown_on
    FROM trips t
    CROSS JOIN LATERAL unnest(string_to_array(t.material_type, ',')) AS code
    WHERE t.user_id = ANY(:user_ids)
        AND t.trip_type IN ('air', 'helicopter')
        AND NOT t.is_project
        AND (
            COALESCE(t.utc_start_datetime, t.start_datetime) IS NULL
            OR COALESCE(t.utc_start_datetime, t.start_datetime) < NOW()
        )
)
{% if codes_only %}
SELECT DISTINCT user_id, code FROM flown
{% else %}
SELECT code, trip_id, trip_length, flown_on FROM flown
{% endif %}
