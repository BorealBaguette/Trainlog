DELETE FROM news_reactions
WHERE news_id = :news_id AND username = :username AND emoji = :emoji
