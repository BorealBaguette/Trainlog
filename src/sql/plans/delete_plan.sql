-- plan_trips (and their inline geom) are removed by ON DELETE CASCADE.
DELETE FROM plans WHERE uid = :uid AND user_id = :user_id
