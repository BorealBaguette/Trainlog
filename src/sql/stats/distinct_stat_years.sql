SELECT DISTINCT 
    EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime))::text AS year
FROM trips
WHERE (:tripType = 'combined' OR trip_type = :tripType)
AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) > 1950
AND COALESCE(utc_start_datetime, start_datetime) IS NOT NULL
AND is_project = false
AND (:user_id IS NULL OR user_id = :user_id)
ORDER BY year DESC
