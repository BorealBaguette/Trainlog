SELECT uid, uuid, user_id, name, description, anchor_date, archived, created, last_modified
FROM plans
WHERE uuid = :uuid
