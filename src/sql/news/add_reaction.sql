INSERT INTO news_reactions (news_id, username, emoji)
VALUES (:news_id, :username, :emoji)
ON CONFLICT (news_id, username, emoji) DO NOTHING
