-- Mark the given news items as seen by this user
INSERT INTO news_views (news_id, username)
SELECT id, :username FROM news WHERE id = ANY(:news_ids)
ON CONFLICT DO NOTHING
