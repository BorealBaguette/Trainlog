-- Reaction summary across every news item, flagging which emojis the current
-- user picked and listing who reacted with each (pass an empty username for
-- anonymous viewers).
SELECT news_id,
       emoji,
       COUNT(*)                            AS cnt,
       BOOL_OR(username = :username)       AS reacted,
       array_agg(username ORDER BY username) AS users
FROM news_reactions
GROUP BY news_id, emoji
ORDER BY news_id, cnt DESC, emoji
