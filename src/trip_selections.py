"""Short share links for trip selections.

A selection of a few hundred trips used to be spelled out id by id in the URL
path. The list is stored in ``trip_selections`` instead and the path carries an
8-character key, so the URL is the same length whether two trips are shared or
four thousand:

    /public/trip/sK7f2Qw9

Keys are marked by a leading ``s`` so they can share the ``<tripIds>`` route
segment with the plain "1,2,3" lists every link handed out so far still uses.
"""

import hashlib
import re
import secrets

from sqlalchemy.exc import IntegrityError

from src.pg import pg_session

# Anything that is not a plain id list is a key: digits and commas can never
# match, so old links keep resolving the old way.
KEY_RE = re.compile(r"^s[0-9A-Za-z]{8}$")

_KEY_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_KEY_LENGTH = 8

# A share link can be created without logging in (the public trip page has the
# same share button), so cap what one row may hold.
MAX_SELECTION = 10000


def is_selection_key(text: str) -> bool:
    return bool(text) and bool(KEY_RE.match(text))


def _new_key() -> str:
    return "s" + "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_LENGTH))


def store_trip_ids(ids) -> str:
    """Return the share key for these trip ids, creating it if it is new.

    The ids are sorted and de-duplicated first, so the same selection always
    lands on the same key however it was picked. Ordering is not preserved —
    every page that reads a selection back sorts it chronologically anyway.
    """
    ids = sorted({int(i) for i in ids})
    if not ids:
        raise ValueError("empty trip selection")
    if len(ids) > MAX_SELECTION:
        raise ValueError(f"trip selection too large: {len(ids)}")

    ids_hash = hashlib.sha256(",".join(str(i) for i in ids).encode()).hexdigest()

    with pg_session() as pg:
        existing = pg.execute(
            "SELECT key FROM trip_selections WHERE ids_hash = :ids_hash",
            {"ids_hash": ids_hash},
        ).fetchone()
        if existing:
            return existing["key"]

    # 62^8 keys, so a collision is vanishingly unlikely — but it is one INSERT,
    # so retrying beats explaining a 500 to whoever hits it.
    for _ in range(5):
        try:
            with pg_session() as pg:
                row = pg.execute(
                    """
                    INSERT INTO trip_selections (key, trip_ids, ids_hash)
                    VALUES (:key, :trip_ids, :ids_hash)
                    ON CONFLICT (ids_hash) DO UPDATE SET ids_hash = EXCLUDED.ids_hash
                    RETURNING key
                    """,
                    {"key": _new_key(), "trip_ids": ids, "ids_hash": ids_hash},
                ).fetchone()
                return row["key"]
        except IntegrityError:
            continue
    raise ValueError("could not allocate a share key")


def resolve_trip_ids(key: str) -> list:
    """Trip ids behind a share key. Raises ValueError if the key is unknown."""
    with pg_session() as pg:
        row = pg.execute(
            "SELECT trip_ids FROM trip_selections WHERE key = :key", {"key": key}
        ).fetchone()
    if row is None:
        raise ValueError(f"unknown trip selection: {key}")
    return list(row["trip_ids"])


def parse_trip_ids(text: str) -> list:
    """Read a <tripIds> URL segment: a share key or a legacy "1,2,3" list."""
    if not text:
        raise ValueError("empty trip id list")
    if is_selection_key(text):
        return resolve_trip_ids(text)
    return [int(part) for part in text.split(",")]
