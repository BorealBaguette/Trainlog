-- Per-user news visit tracking, replacing the last_news_visit browser cookie.
-- username mirrors news.username (users live in the SQLite auth DB, no FK possible).
CREATE TABLE news_visits (
    username TEXT PRIMARY KEY,
    last_visit TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
