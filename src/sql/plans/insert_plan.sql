INSERT INTO plans (uuid, user_id, name, description, anchor_date, created, last_modified)
VALUES (:uuid, :user_id, :name, :description, :anchor_date, :created, :last_modified)
RETURNING uid, uuid
