-- Channel webhooks users add so their announced trips also go to their own
-- Discord servers.
CREATE TABLE user_discord_webhooks (
    id      SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    label   TEXT NOT NULL,
    url     TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    -- Whether trips go here by themselves at departure, rather than only when
    -- the user posts one by hand.
    autopost BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (user_id, url)
);

-- The message each webhook received for a trip, kept so the post can be taken
-- down again (a webhook can delete its own messages, given the message id).
CREATE TABLE trip_announcement_posts (
    trip_id    INTEGER NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
    webhook_id INTEGER NOT NULL REFERENCES user_discord_webhooks(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    PRIMARY KEY (trip_id, webhook_id)
);
