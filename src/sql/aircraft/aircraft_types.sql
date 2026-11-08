-- Every aircraft type Trainlog knows, with how often the user has flown on it.
-- Types never flown still come back (flights = 0) so the page can show them
-- blacked out.
--
-- Air trips keep the ICAO type code in material_type; a trip can list several
-- comma-separated codes (a change of aircraft on the same flight number), and
-- each counts. Only flights already taken: no projects, nothing in the future.
WITH flown AS (
    SELECT DISTINCT
        UPPER(TRIM(code)) AS code,
        t.trip_id,
        t.trip_length,
        COALESCE(t.utc_start_datetime, t.start_datetime) AS flown_at
    FROM trips t
    CROSS JOIN LATERAL unnest(string_to_array(t.material_type, ',')) AS code
    WHERE t.user_id = :user_id
        AND t.trip_type IN ('air', 'helicopter')
        AND NOT t.is_project
        AND (
            COALESCE(t.utc_start_datetime, t.start_datetime) IS NULL
            OR COALESCE(t.utc_start_datetime, t.start_datetime) < NOW()
        )
)
-- One row per type with its trips listed rather than summed: the page also
-- totals whole families, and a trip naming two types of the same family must
-- only count once there.
SELECT
    a.iata AS code,
    a.manufacturer,
    a.model,
    COALESCE(ARRAY_AGG(f.trip_id) FILTER (WHERE f.trip_id IS NOT NULL), '{}') AS trip_ids,
    COALESCE(ARRAY_AGG(f.trip_length) FILTER (WHERE f.trip_id IS NOT NULL), '{}') AS trip_lengths,
    COALESCE(ARRAY_AGG(f.flown_at::date) FILTER (WHERE f.trip_id IS NOT NULL), '{}') AS flown_on
FROM airliners a
LEFT JOIN flown f ON f.code = UPPER(a.iata)
GROUP BY a.iata, a.manufacturer, a.model
