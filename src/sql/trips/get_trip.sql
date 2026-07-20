-- PostgreSQL. Single trip with a textual `time` state (past/plannedFuture/current/
-- future), operator name and the era-appropriate operator logo. Row is adapted back
-- to the legacy SQLite `trip` shape in Python (adapt_pg_trip_row); username is
-- resolved from user_id by the caller (auth lives in a separate SQLite DB).
WITH base AS (
    SELECT trips.*,
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime
    FROM trips
)
SELECT
    base.*,
    -- Air trips store the ICAO type code in material_type; the readable name lives
    -- in `airliners` (matches ~95% of logged types, NULL for the rest).
    airliners.manufacturer,
    airliners.model,
    -- Ferry flag state. Aircraft derive their country from the registration prefix
    -- client-side, but a ship's only lives in ship_pictures, so surface it here —
    -- otherwise the flag could not appear until the photo had been fetched.
    sp.country_code AS vessel_country,
    CASE
        WHEN NOW() > base.utc_filtered_end_datetime
             OR (base.utc_filtered_start_datetime IS NULL AND NOT base.is_project)
        THEN 'past'
        WHEN base.utc_filtered_start_datetime IS NOT NULL
             AND NOW() <= base.utc_filtered_start_datetime
        THEN 'plannedFuture'
        WHEN NOW() BETWEEN base.utc_filtered_start_datetime AND base.utc_filtered_end_datetime
        THEN 'current'
        WHEN base.utc_filtered_start_datetime IS NULL AND base.is_project
        THEN 'future'
    END AS "time",
    o.short_name AS operator_name,
    CASE
        -- Unknown past date (sentinel -1): oldest logo.
        WHEN base.utc_filtered_start_datetime IS NULL AND NOT base.is_project THEN (
            SELECT l.logo_url
            FROM operator_logos l
            WHERE l.operator_id = o.operator_id
            ORDER BY l.effective_date ASC NULLS FIRST
            LIMIT 1
        )
        -- Project/future (sentinel 1): latest logo.
        WHEN base.utc_filtered_start_datetime IS NULL AND base.is_project THEN (
            SELECT l.logo_url
            FROM operator_logos l
            WHERE l.operator_id = o.operator_id
            ORDER BY l.effective_date DESC NULLS LAST
            LIMIT 1
        )
        -- Real date: logo closest to (and not after) the trip start.
        ELSE (
            SELECT l.logo_url
            FROM operator_logos l
            WHERE l.operator_id = o.operator_id
              AND (l.effective_date <= base.utc_filtered_start_datetime OR l.effective_date IS NULL)
            ORDER BY l.effective_date DESC NULLS LAST
            LIMIT 1
        )
    END AS logo_url
FROM base
LEFT JOIN airliners ON base.material_type = airliners.iata
-- vessel_name is not unique (559 rows / 536 names), so a plain join would return
-- duplicate rows for one trip. Duplicates agree on country_code, so any one row will
-- do — pick the newest deterministically. Matched exactly, like get_vessel_picture.
LEFT JOIN LATERAL (
    SELECT country_code
    FROM ship_pictures
    WHERE vessel_name = base.reg
    ORDER BY fetch_date DESC NULLS LAST, uid DESC
    LIMIT 1
) sp ON TRUE
-- short_name is not unique (e.g. two "SNCB" rows), so a plain join would return
-- duplicate rows for one trip. Pick one operator deterministically.
LEFT JOIN LATERAL (
    SELECT operator_id, short_name
    FROM operators
    WHERE short_name = base.operator
    ORDER BY operator_id
    LIMIT 1
) o ON TRUE
WHERE base.trip_id = :trip_id;
