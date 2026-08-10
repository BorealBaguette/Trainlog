INSERT INTO commute_routes (user_id, name)
VALUES (:user_id, :name)
RETURNING uid
