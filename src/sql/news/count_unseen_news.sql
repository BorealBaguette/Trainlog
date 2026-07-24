-- Users without a news_visits row count as having seen everything up to the
-- moment the news_visits migration was applied
SELECT COUNT(*)
FROM news
WHERE created > COALESCE(
    (SELECT last_visit FROM news_visits WHERE username = :username),
    (SELECT applied FROM meta.migrations WHERE name = '0048_news_visits.sql')
)
