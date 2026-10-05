-- New webhooks start as manual only: auto-posting broadcasts where someone is
-- right now, so it has to be asked for. Existing rows keep their setting.
-- ADD COLUMN IF NOT EXISTS covers a database that ran 0074 before it carried
-- the autopost column.
ALTER TABLE user_discord_webhooks ADD COLUMN IF NOT EXISTS autopost BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE user_discord_webhooks ALTER COLUMN autopost SET DEFAULT FALSE;
