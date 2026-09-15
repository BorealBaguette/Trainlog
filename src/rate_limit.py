"""A call allowance shared by every worker, for the APIs we are a guest of.

Kept in Postgres because that is the only state four gunicorn workers and their background
threads have in common — see migration 0066.
"""

import logging

from sqlalchemy import text

import src.pg as pg

logger = logging.getLogger(__name__)

# (tokens per second, bucket size) per service. The rate is the sustained ceiling across the
# whole deployment; the bucket is how much of it may be spent at once.
LIMITS = {
    # Matches the pause the enricher already keeps between batches, so normal draining is
    # unaffected and only a pile-up of admins is held back.
    "overpass": (0.5, 10),
    "wikidata": (0.5, 5),
}

# Spend a token if the bucket has one, refilling it for the time since the last call. Written
# as one statement so the read and the write cannot be split by another worker.
_TAKE = text(
    """
    INSERT INTO external_api_budget AS b (service, tokens, updated_at)
    VALUES (:service, :burst - 1, now())
    ON CONFLICT (service) DO UPDATE
       SET tokens = LEAST(:burst, b.tokens
                          + EXTRACT(EPOCH FROM (now() - b.updated_at)) * :rate) - 1,
           updated_at = now()
     WHERE LEAST(:burst, b.tokens
                 + EXTRACT(EPOCH FROM (now() - b.updated_at)) * :rate) >= 1
    RETURNING tokens
    """
)


class RateLimited(Exception):
    """The shared allowance for this service is spent."""


def take(service):
    """Consume one call from `service`'s allowance, or raise RateLimited.

    Runs on its own connection and commits immediately: joining the caller's transaction would
    hand the tokens back whenever that transaction rolled back, which is exactly when a caller
    is retrying and the limit matters most.
    """
    rate, burst = LIMITS[service]
    pg.init_db_engine()
    with pg.pg_session_engine.begin() as connection:
        spent = connection.execute(
            _TAKE, {"service": service, "rate": rate, "burst": burst}
        ).fetchone()
    if spent is None:
        raise RateLimited(
            f"{service} allowance is spent — it allows {rate:g} call(s) per second across the "
            f"whole site. Try again shortly"
        )
