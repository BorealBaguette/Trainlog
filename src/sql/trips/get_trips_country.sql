-- Completed (non-future, non-planned) land trips for a user, filtered by country.
WITH base AS (
    SELECT trips.*,
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime
    FROM trips
    WHERE user_id = :user_id
      AND trip_type IN ('train', 'tram', 'metro')
)
SELECT trip_id AS uid
FROM base
WHERE NOT (utc_filtered_start_datetime IS NULL AND is_project)          -- future = 0
  AND NOT (utc_filtered_start_datetime IS NOT NULL
           AND NOW() <= utc_filtered_start_datetime)                    -- plannedFuture = 0
  AND (
      CASE
          WHEN :country LIKE '%CN%' OR :country LIKE '%HK%' OR :country LIKE '%MO%'
          THEN (countries LIKE '%CN%' OR countries LIKE '%HK%' OR countries LIKE '%MO%')
          ELSE countries LIKE :country
      END
  )
