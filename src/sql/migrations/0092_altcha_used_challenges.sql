-- Signup captcha (ALTCHA): each solved challenge counts once. A row lives until
-- the challenge would have expired anyway, then gets swept on a later signup.
CREATE TABLE IF NOT EXISTS altcha_used_challenges (
    nonce TEXT PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL
);
