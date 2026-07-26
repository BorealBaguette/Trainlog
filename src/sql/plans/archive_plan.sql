UPDATE plans
SET archived = :archived,
    last_modified = :last_modified
WHERE uid = :uid AND user_id = :user_id
