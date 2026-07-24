-- Uses the database clock so last_visit is comparable to news.created
INSERT INTO news_visits (username, last_visit)
VALUES (:username, now())
ON CONFLICT (username) DO UPDATE SET last_visit = now()
