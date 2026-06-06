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
