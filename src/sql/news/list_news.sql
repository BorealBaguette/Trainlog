-- A NULL :limit means no limit, for callers that want every item
SELECT id, title, content, username, created, last_modified
FROM news
ORDER BY created DESC
LIMIT :limit OFFSET :offset
