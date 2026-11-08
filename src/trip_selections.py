"""Short share links for trip selections.

A selection of a few hundred trips used to be spelled out id by id in the URL
path. The list is stored in ``trip_selections`` instead and the path carries an
8-character key, so the URL is the same length whether two trips are shared or
four thousand:

    /public/trip/sK7f2Qw9

Keys are marked by a leading ``s`` so they can share the ``<tripIds>`` route
segment with the plain "1,2,3" lists every link handed out so far still uses.

A key can also stand for a tag (``tag_uuid``): it then resolves to whatever trips
the tag holds at that moment instead of a stored list.
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


def store_trip_ids(ids, share_flags=None) -> str:
    """Return the share key for these trip ids, creating it if it is new.

    The ids are sorted and de-duplicated first, so the same selection always
    lands on the same key however it was picked. Ordering is not preserved —
    every page that reads a selection back sorts it chronologically anyway.

    `share_flags` ('p', 'c', 'pc' or 'n', see src/share_view.py) bakes what the
    link shows into the key; None leaves it to the owner's settings. The flags
    are part of the hash, so each choice is a key of its own.
    """
    ids = sorted({int(i) for i in ids})
    if not ids:
        raise ValueError("empty trip selection")
    if len(ids) > MAX_SELECTION:
        raise ValueError(f"trip selection too large: {len(ids)}")

    hashed = ",".join(str(i) for i in ids)
    if share_flags is not None:
        hashed += "|" + share_flags
    return _store(hashlib.sha256(hashed.encode()).hexdigest(), ids, share_flags)


def store_tag_key(tag_uuid, share_flags) -> str:
    """Return the share key showing `share_flags` for this tag, creating it if new."""
    ids_hash = hashlib.sha256(f"tag:{tag_uuid}|{share_flags}".encode()).hexdigest()
    return _store(ids_hash, [], share_flags, tag_uuid)


def _store(ids_hash, ids, share_flags, tag_uuid=None) -> str:
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
                    INSERT INTO trip_selections (key, trip_ids, ids_hash, share_flags, tag_uuid)
                    VALUES (:key, :trip_ids, :ids_hash, :share_flags, :tag_uuid)
                    ON CONFLICT (ids_hash) DO UPDATE SET ids_hash = EXCLUDED.ids_hash
                    RETURNING key
                    """,
                    {"key": _new_key(), "trip_ids": ids, "ids_hash": ids_hash,
                     "share_flags": share_flags, "tag_uuid": tag_uuid},
                ).fetchone()
                return row["key"]
        except IntegrityError:
            continue
    raise ValueError("could not allocate a share key")


def resolve_trip_ids(key: str) -> list:
    """Trip ids behind a share key. Raises ValueError if the key is unknown or its
    tag is gone or empty."""
    with pg_session() as pg:
        row = pg.execute(
            """
            SELECT CASE WHEN s.tag_uuid IS NULL THEN s.trip_ids ELSE ARRAY(
                       SELECT ta.trip_id
                       FROM tags_associations ta
                       JOIN tags t ON t.uid = ta.tag_id
                       WHERE t.uuid = s.tag_uuid
                   ) END AS trip_ids
            FROM trip_selections s
            WHERE s.key = :key
            """,
            {"key": key},
        ).fetchone()
    if row is None or not row["trip_ids"]:
        raise ValueError(f"unknown trip selection: {key}")
    return list(row["trip_ids"])


def selection_tag(text):
    """Name, type and shared state of the tag behind a share key, or None."""
    if not is_selection_key(text or ""):
        return None
    with pg_session() as pg:
        return pg.execute(
            """
            SELECT t.name, t.type, EXISTS (
                       SELECT 1 FROM tag_members tm
                       WHERE tm.tag_id = t.uid AND tm.status = 'accepted'
                   ) AS shared
            FROM trip_selections s
            JOIN tags t ON t.uuid = s.tag_uuid
            WHERE s.key = :key
            """,
            {"key": text},
        ).fetchone()


def selection_share_flags(text):
    """The prices / CO₂ choice baked into a share key ('p', 'c', 'pc', 'n'), or
    None for a plain key, an unknown one, or a legacy "1,2,3" list."""
    if not is_selection_key(text or ""):
        return None
    with pg_session() as pg:
        row = pg.execute(
            "SELECT share_flags FROM trip_selections WHERE key = :key", {"key": text}
        ).fetchone()
    return row["share_flags"] if row else None


def parse_trip_ids(text: str) -> list:
    """Read a <tripIds> URL segment: a share key or a legacy "1,2,3" list."""
    if not text:
        raise ValueError("empty trip id list")
    if is_selection_key(text):
        return resolve_trip_ids(text)
    return [int(part) for part in text.split(",")]
