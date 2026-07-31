-- Members of shared tags. username mirrors tags.username (users live in the
-- SQLite auth DB, so no FK is possible; follows tags/tags_associations conventions).
CREATE TABLE tag_members (
    tag_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted')),
    invited_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    responded_at TIMESTAMP,
    PRIMARY KEY (tag_id, username)
);

CREATE INDEX tag_members_username_idx ON tag_members (username);
