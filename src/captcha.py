"""Self-hosted signup captcha (ALTCHA proof-of-work), replacing FriendlyCaptcha.

The browser fetches a signed challenge when the signup page loads and solves it
in a background worker; the form posts the solution as the `altcha` field.
Nothing leaves our server, and the only secret is derived from the Flask key.
"""

import hashlib
import hmac
import secrets
import time

from altcha import Payload, create_challenge, verify_solution

from src.pg import pg_session

# PBKDF2 rounds per attempt x attempts: about 1-2 s on a laptop core, a few
# seconds on a phone, all while the person is still typing
COST = 1000
MIN_COUNTER = 2000
COUNTER_SPREAD = 3000
TTL_SECONDS = 3600
# A challenge solved and posted sooner than this after the page loaded was not
# filled in by a person
MIN_FILL_SECONDS = 3


def _secrets(app_secret):
    def derive(purpose):
        return hmac.new(app_secret.encode(), purpose, hashlib.sha256).hexdigest()

    return derive(b"altcha-challenge"), derive(b"altcha-key")


def new_challenge(app_secret):
    hmac_secret, hmac_key_secret = _secrets(app_secret)
    challenge = create_challenge(
        algorithm="PBKDF2/SHA-256",
        cost=COST,
        counter=MIN_COUNTER + secrets.randbelow(COUNTER_SPREAD),
        expires_at=int(time.time()) + TTL_SECONDS,
        hmac_secret=hmac_secret,
        hmac_key_secret=hmac_key_secret,
    )
    return challenge.to_dict()


def check_solution(payload, app_secret):
    """Return None when the payload is a fresh, valid solution, else why not
    (the suspicious-activity reason)."""
    if not payload:
        return "no_captcha"
    hmac_secret, hmac_key_secret = _secrets(app_secret)
    result = verify_solution(payload, hmac_secret, hmac_key_secret=hmac_key_secret)
    if not result.verified:
        return "captcha_expired" if result.expired else "captcha_failed"

    params = Payload.from_base64(payload).challenge.parameters
    issued_at = params.expires_at - TTL_SECONDS
    if time.time() - issued_at < MIN_FILL_SECONDS:
        return "captcha_too_fast"

    with pg_session() as pg:
        pg.execute("DELETE FROM altcha_used_challenges WHERE expires_at < now()")
        inserted = pg.execute(
            "INSERT INTO altcha_used_challenges (nonce, expires_at) "
            "VALUES (:nonce, to_timestamp(:expires_at)) ON CONFLICT DO NOTHING",
            {"nonce": params.nonce, "expires_at": params.expires_at},
        ).rowcount
    return None if inserted else "captcha_replayed"
