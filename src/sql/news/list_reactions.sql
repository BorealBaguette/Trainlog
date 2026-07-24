-- Reaction summary for a single news item, flagging which emojis the current
-- user picked and listing who reacted with each (pass an empty username for
-- anonymous viewers).
SELECT emoji,
       COUNT(*)                            AS cnt,
       BOOL_OR(username = :username)       AS reacted,
       array_agg(username ORDER BY username) AS users
FROM news_reactions
WHERE news_id = :news_id
GROUP BY emoji
ORDER BY cnt DESC, emoji
