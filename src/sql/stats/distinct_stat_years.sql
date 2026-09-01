SELECT DISTINCT 
    EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime))::text AS year
FROM trips
WHERE (:tripType = 'combined' OR trip_type = :tripType)
-- Same bounds as stats_year.sql / stats_months.sql. Without the upper one the
-- dropdown offers years (mistyped dates land on 2126, 3000, 9999) that every
-- chart filters out, so picking one renders an empty page. The ceiling is
-- relative to today so it keeps covering genuine future plans as time passes.
AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) > 1950
AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) <= EXTRACT(YEAR FROM NOW()) + 15
AND COALESCE(utc_start_datetime, start_datetime) IS NOT NULL
AND is_project = false
AND (:user_id IS NULL OR user_id = :user_id)
ORDER BY year DESC
