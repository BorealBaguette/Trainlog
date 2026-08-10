SELECT uid, user_id, name, archived, created, last_modified
FROM commute_routes
WHERE uid = :uid AND user_id = :user_id
