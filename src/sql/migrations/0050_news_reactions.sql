-- Emoji reactions on news items. One row per (news item, user, emoji); the
-- primary key lets a user pick several distinct emojis but not double-count one.
-- username mirrors news.username (users live in the SQLite auth DB, no FK possible).
CREATE TABLE news_reactions (
    news_id  INTEGER NOT NULL REFERENCES news(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    emoji    TEXT NOT NULL,
    created  TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (news_id, username, emoji)
);
CREATE INDEX idx_news_reactions_news ON news_reactions (news_id);
