-- Unique viewers per news item. One row per (news item, user), inserted the
-- first time a logged-in user loads the news page while the item exists.
-- username mirrors news.username (users live in the SQLite auth DB, no FK possible).
CREATE TABLE news_views (
    news_id  INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    created  TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (news_id, username)
);
