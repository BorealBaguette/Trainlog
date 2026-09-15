SELECT news_id, COUNT(*) AS views
FROM news_views
GROUP BY news_id
