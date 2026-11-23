SELECT uid, uuid, user_id, name, description, anchor_date, archived, visibility,
       validated_tag_uuid, created, last_modified, pass_days_enabled
FROM plans
WHERE uuid = :uuid
