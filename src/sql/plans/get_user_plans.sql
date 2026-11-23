-- Plans for a user, with a trip count + aggregate stats, ordered active-first then most-recent.
-- validated_count is the number of legs already logged as real trips (partial validation),
-- so the list can show how far a plan has been logged. start_date: when the plan begins.
SELECT p.uid, p.uuid, p.user_id, p.name, p.description, p.anchor_date,
       p.archived, p.visibility, p.validated_tag_uuid, p.created, p.last_modified,
       p.pass_days_enabled,
       COUNT(pt.uid) AS trip_count,
       COUNT(pt.uid) FILTER (WHERE pt.validated_trip_id IS NOT NULL) AS validated_count,
       COALESCE(SUM(pt.trip_length), 0) / 1000.0 AS total_distance_km,
       COALESCE(SUM(COALESCE(pt.manual_trip_duration, pt.estimated_trip_duration)), 0) AS total_duration_s,
       -- First day of the plan: its earliest dated leg, or Day N placed on the Day-1
       -- date, whichever comes first (LEAST skips the NULL of a plan without one).
       LEAST(
           CAST(MIN(pt.start_datetime) FILTER (WHERE pt.start_day IS NULL) AS date),
           p.anchor_date + (MIN(pt.start_day) - 1)
       ) AS start_date
FROM plans p
LEFT JOIN plan_trips_resolved pt ON pt.plan_id = p.uid
WHERE p.user_id = :user_id
GROUP BY p.uid
ORDER BY p.archived, p.last_modified DESC NULLS LAST, p.uid DESC
