-- One row per commute, aggregated across its linked trips. Modeled on the
-- tag_list aggregation query (app.py `tag_list`).
WITH UTC_Filtered AS (
    SELECT *,
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime
    FROM trips
)
SELECT c.uid,
       c.name,
       c.created,
       c.last_modified,
       COUNT(DISTINCT crt.trip_id) AS trip_count,
       MAX(uf.utc_filtered_end_datetime) AS latest_trip_end,
       SUM(
           CASE
               WHEN uf.utc_filtered_start_datetime IS NOT NULL
                    AND uf.utc_filtered_end_datetime IS NOT NULL
               THEN EXTRACT(EPOCH FROM (uf.utc_filtered_end_datetime - uf.utc_filtered_start_datetime))
               WHEN uf.manual_trip_duration IS NOT NULL
               THEN uf.manual_trip_duration
               ELSE uf.estimated_trip_duration
           END
       ) AS total_trip_duration,
       SUM(uf.trip_length) AS total_trip_length
FROM commute_routes c
LEFT JOIN commute_occurrences co ON co.commute_route_id = c.uid
LEFT JOIN commute_route_trips crt ON crt.occurrence_id = co.uid
LEFT JOIN UTC_Filtered uf ON crt.trip_id = uf.trip_id
WHERE c.user_id = :user_id AND c.archived = FALSE
GROUP BY c.uid
ORDER BY
    CASE WHEN MAX(uf.utc_filtered_end_datetime) IS NULL THEN 1 ELSE 0 END,
    MAX(uf.utc_filtered_end_datetime) DESC NULLS LAST
