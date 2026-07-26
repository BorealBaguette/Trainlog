UPDATE plans
SET name = :name,
    description = :description,
    anchor_date = :anchor_date,
    last_modified = :last_modified
WHERE uid = :uid AND user_id = :user_id
