-- Plans for a user, with a trip count, ordered active-first then most-recent.
SELECT p.uid, p.uuid, p.user_id, p.name, p.description, p.anchor_date,
       p.archived, p.created, p.last_modified,
       COUNT(pt.uid) AS trip_count
FROM plans p
LEFT JOIN plan_trips pt ON pt.plan_id = p.uid
WHERE p.user_id = :user_id
GROUP BY p.uid
ORDER BY p.archived, p.last_modified DESC NULLS LAST, p.uid DESC
