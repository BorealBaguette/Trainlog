import os
from datetime import datetime
from typing import Iterable, Literal, NotRequired, TypedDict

from src.pg import get_or_create_pg_session, pg_session
from src.sql.operators import (
    get_trip_operator_logos_query,
    insert_trip_operators_query,
    resolve_operator_names_query,
)

LOGO_UPLOAD_FOLDER = "static/images/operator_logos/new"

# Trip types that keep their own pool of operators. Every other type (train, bus,
# air, ferry, …) shares the 'operator' pool, so that "Avis" the car-rental company
# stays distinct from "Avis" the transport operator. Mirrors operator_type_bucket()
# in migration 0042 — keep the two in step.
OWN_POOL_TRIP_TYPES = frozenset(["accommodation", "car", "poi"])

# The pools an operator can belong to (operators.operator_type).
OPERATOR_TYPES = ("operator", "accommodation", "car", "poi")


def operator_bucket(trip_type: str | None) -> str:
    """Return the operators pool a trip type draws from."""
    return trip_type if trip_type in OWN_POOL_TRIP_TYPES else "operator"


def split_operators(raw: str | None) -> list[str]:
    """Split a `trips.operator` value into the individual names as typed.

    The single definition of how the comma-separated field is read; it previously
    existed in four different forms across the SQL and the Python.
    """
    if not raw:
        return []
    return [name for name in (part.strip() for part in str(raw).split(",")) if name]


class OperatorInfo(TypedDict):
    operator_id: int
    short_name: str
    long_name: str
    slug: str
    operator_type: str


def resolve_operators(
    names: Iterable[str], trip_type: str, pg_session_=None
) -> dict[str, OperatorInfo]:
    """Map written operator names to canonical operators, case/accent-insensitively.

    Missing keys mean the spelling matches no alias. One query per call, whatever the
    number of names.
    """
    names = list(dict.fromkeys(n for n in names if n and n.strip()))
    if not names:
        return {}

    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            resolve_operator_names_query(),
            {"names": names, "operator_type": operator_bucket(trip_type)},
        ).fetchall()

    return {
        row["input_name"]: {
            "operator_id": row["operator_id"],
            "short_name": row["short_name"],
            "long_name": row["long_name"],
            "slug": row["slug"],
            "operator_type": row["operator_type"],
        }
        for row in rows
        if row["operator_id"] is not None
    }


def find_operator_ids(name: str, exact: bool = False, pg_session_=None) -> list[int]:
    """Operators whose canonical name or any alias matches `name`.

    Lets trip search follow aliases: searching "SBB" also finds trips logged as CFF
    or FFS. Matching is diacritic-insensitive, like the rest of the search.

    Returns an empty list when nothing matches, in which case callers should fall
    back to plain text matching on the raw operator field alone.
    """
    if not name or not name.strip():
        return []

    pattern = name.strip() if exact else f"%{name.strip()}%"
    with get_or_create_pg_session(pg_session_) as pg:
        # Expanded to the whole equivalence group: searching SBB must also find trips
        # logged as CFF, exactly as it does for an alias of the same operator.
        rows = pg.execute(
            """
            WITH matched AS (
                SELECT DISTINCT a.operator_id
                FROM operator_aliases a
                WHERE remove_diacritics(LOWER(a.alias))
                      LIKE remove_diacritics(LOWER(:pattern))
            )
            SELECT DISTINCT peer.operator_id
            FROM matched
            JOIN operators o ON o.operator_id = matched.operator_id
            JOIN operators peer
              ON peer.operator_id = o.operator_id
              OR (o.group_id IS NOT NULL AND peer.group_id = o.group_id)
            """,
            {"pattern": pattern},
        ).fetchall()
    return [row[0] for row in rows]


def get_trip_operator_logos(
    trip_id: int, trip_start, pg_session_=None
) -> list[dict[str, str]]:
    """The trip's operators with their era-appropriate logos, in the order typed.

    `trip_start` is the adapted trip's utc_filtered_start_datetime: the sentinel -1
    for a past trip with an unknown date, 1 for a project/future trip, otherwise a
    real datetime (see adapt_pg_trip_row). Both sentinels mean "no date to choose
    by", so both take the current logo.

    Operators with no logo are left out: the trip page renders an <img> for every
    entry it is given, so an entry without a URL would show a broken image.
    """
    if trip_start in (-1, 1):
        mode, start = "latest", None
    else:
        mode, start = "at_date", trip_start

    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            get_trip_operator_logos_query(),
            {"trip_id": trip_id, "mode": mode, "start": start},
        ).fetchall()

    return [
        # The name as the user typed it, not the canonical spelling — an operator
        # logged as CFF shows the SBB logo but is still labelled CFF.
        {"operator_name": row["raw_name"], "logo_url": row["logo_url"]}
        for row in rows
        if row["logo_url"]
    ]


def sync_trip_operators(
    trip_ids: int | str | Iterable[int | str], pg_session_=None
) -> None:
    """Refresh the derived trip_operators rows for the given trips.

    Accepts a single id (int or str, as request forms deliver it) or a collection.

    Called from every path that writes `trips.operator`. Safe to call for trips with
    no operator, and idempotent — it rewrites the rows from the trip's current text.
    """
    # A single id may arrive as a string — trip_id comes straight off the request
    # form in the update/copy routes. Testing for int alone let a string through to
    # the iterable branch, where "824030" iterated into [8, 2, 4, 0, 3, 0]: the trip
    # being saved was silently skipped and unrelated trips were re-synced instead.
    if not isinstance(trip_ids, (list, tuple, set)):
        trip_ids = [trip_ids]
    trip_ids = [int(t) for t in trip_ids]
    if not trip_ids:
        return

    with get_or_create_pg_session(pg_session_) as pg:
        pg.execute(
            "DELETE FROM trip_operators WHERE trip_id = ANY(:trip_ids)",
            {"trip_ids": trip_ids},
        )
        pg.execute(insert_trip_operators_query(scoped=True), {"trip_ids": trip_ids})


def rebuild_trip_operators(pg_session_=None) -> int:
    """Rebuild trip_operators from scratch for every trip.

    trip_operators is purely derived, so this is the recovery path if a write ever
    escapes sync_trip_operators(), and it is how an alias change is applied to
    historical trips. Returns the number of rows written.
    """
    with get_or_create_pg_session(pg_session_) as pg:
        pg.execute("DELETE FROM trip_operators")
        pg.execute(insert_trip_operators_query(scoped=False))
        return pg.execute("SELECT count(*) FROM trip_operators").scalar()


def resync_operator_aliases(operator_id: int, pg_session_=None) -> int:
    """Re-resolve the trips affected by an alias change on one operator.

    Deliberately narrow, so editing an operator's spellings never costs a full
    rebuild:

    - trips already pointing at this operator (an alias may have been removed from
      under them, or the operator merged away), plus
    - still-unresolved trips whose spelling matches *any* of the operator's current
      aliases — the trips its spellings are meant to capture.

    Matching against the whole alias set, not a single passed-in name, is what makes
    a rename safe: reinstating a long name the operator had lost must re-resolve the
    trips using it, even when the field just edited was the short name.

    Returns the number of trips re-synced.
    """
    with get_or_create_pg_session(pg_session_) as pg:
        trip_ids = [
            row[0]
            for row in pg.execute(
                """
                SELECT DISTINCT trip_id FROM trip_operators
                WHERE operator_id = :operator_id
                   OR (
                        operator_id IS NULL
                        AND operator_normalize(raw_name) IN (
                            SELECT normalized FROM operator_aliases
                            WHERE operator_id = :operator_id
                        )
                   )
                """,
                {"operator_id": operator_id},
            ).fetchall()
        ]
        sync_trip_operators(trip_ids, pg_session_=pg)
        return len(trip_ids)


def check_trip_operators_consistency(pg_session_=None) -> dict:
    """Compare trip_operators against a fresh resolution of `trips.operator`.

    trip_operators is derived, so any write that escapes sync_trip_operators() goes
    silently wrong rather than raising. This is the detector; rebuild_trip_operators()
    is the cure.
    """
    with get_or_create_pg_session(pg_session_) as pg:
        pg.execute("DROP TABLE IF EXISTS expected_trip_operators")
        pg.execute(
            "CREATE TEMP TABLE expected_trip_operators ON COMMIT DROP AS "
            + str(insert_trip_operators_query(scoped=False, as_select=True))
        )
        missing = pg.execute(
            "SELECT count(*) FROM (SELECT trip_id, position, raw_name, operator_id"
            " FROM expected_trip_operators EXCEPT SELECT trip_id, position, raw_name,"
            " operator_id FROM trip_operators) d"
        ).scalar()
        stale = pg.execute(
            "SELECT count(*) FROM (SELECT trip_id, position, raw_name, operator_id"
            " FROM trip_operators EXCEPT SELECT trip_id, position, raw_name,"
            " operator_id FROM expected_trip_operators) d"
        ).scalar()
    return {"missing": missing, "stale": stale, "consistent": not (missing or stale)}


class OperatorsRepository:
    @classmethod
    def get_operators(cls):
        with pg_session() as pg:
            # `aliases` carries the extra spellings so the admin table can be searched
            # by them. Without it a merged spelling becomes unfindable: after folding
            # CFF into SBB there is no CFF row left, and nothing tells you where it
            # went. Only kind='alias' — short/long already have their own columns.
            result = pg.execute("""
                SELECT o.operator_id, o.short_name, o.long_name, o.operator_type, ol.logo_url,
                       COALESCE((
                           SELECT string_agg(a.alias, ', ' ORDER BY a.alias)
                           FROM operator_aliases a
                           WHERE a.operator_id = o.operator_id AND a.kind = 'alias'
                       ), '') AS aliases,
                       o.group_id,
                       -- The other operators counted as one with this one. Searchable
                       -- for the same reason as aliases: otherwise a grouped operator
                       -- gives no hint of where its trips are being counted.
                       COALESCE((
                           SELECT string_agg(g.short_name, ', ' ORDER BY g.short_name)
                           FROM operators g
                           WHERE g.group_id = o.group_id AND g.operator_id <> o.operator_id
                       ), '') AS group_with
                FROM operators o
                LEFT JOIN operator_logos ol ON ol.operator_id = o.operator_id AND ol.uid = (SELECT MAX(uid) FROM operator_logos WHERE operator_id=o.operator_id)
                ORDER BY o.short_name DESC
            """)
            columns = [
                "operator_id",
                "short_name",
                "long_name",
                "operator_type",
                "logo_url",
                "aliases",
                "group_id",
                "group_with",
            ]
            return [dict(zip(columns, row)) for row in result.fetchall()]

    @classmethod
    def operator_exists(cls, operator_id: int):
        with pg_session() as pg:
            result = pg.execute(
                "SELECT 1 FROM operators WHERE operator_id = :operator_id",
                {"operator_id": operator_id},
            ).fetchone()
            return result is not None

    @classmethod
    def add(cls, short_name: str, long_name: str, operator_type: str):
        """Create an operator, with the slug and alias rows it needs to be usable.

        An operator is only ever found through operator_aliases, so creating one
        without its own names recorded there would make it invisible to resolution:
        trips naming it would stay unresolved and it would never show its logo.
        """
        with pg_session() as pg:
            # Slugs are the natural key for the CSV round-trip and must be unique;
            # disambiguate with a counter exactly as migration 0042 did.
            base = pg.execute(
                "SELECT COALESCE(operator_normalize(:n), 'operator')", {"n": short_name}
            ).scalar()
            slug, n = base, 1
            while pg.execute(
                "SELECT 1 FROM operators WHERE slug = :s", {"s": slug}
            ).fetchone():
                n += 1
                slug = f"{base}-{n}"

            result = pg.execute(
                """
                INSERT INTO operators (short_name, long_name, operator_type, slug)
                VALUES (:short_name, :long_name, :operator_type, :slug)
                RETURNING operator_id, short_name, long_name, operator_type, slug
            """,
                {
                    "short_name": short_name,
                    "long_name": long_name,
                    "operator_type": operator_type,
                    "slug": slug,
                },
            )
            columns = [
                "operator_id",
                "short_name",
                "long_name",
                "operator_type",
                "slug",
            ]
            operator = dict(zip(columns, result.fetchone()))

            operator_id = operator["operator_id"]
            # Same reconcile the rename path uses, so the short/long alias rows are
            # created one way only.
            cls._reconcile_name_aliases(pg, operator_id, operator_type)
            # Trips already spelling it this way resolve to it from now on.
            operator["trips_resynced"] = resync_operator_aliases(
                operator_id, pg_session_=pg
            )
        return operator

    @classmethod
    def name_conflict(cls, short_name: str, operator_type: str, exclude_id: int = None):
        """The operator already using this name in the same pool, if any.

        A name identifies exactly one operator per pool, so two genuinely different
        companies called "Citylink" must be told apart in their names ("Scottish
        Citylink"). This reports the clash up front instead of letting the unique
        index fail, or — worse — creating a second operator that silently never
        resolves.
        """
        with pg_session() as pg:
            row = pg.execute(
                """
                SELECT o.operator_id, o.short_name, o.long_name, a.alias
                FROM operator_aliases a
                JOIN operators o ON o.operator_id = a.operator_id
                WHERE a.operator_type = :operator_type
                  AND a.normalized = operator_normalize(:short_name)
                  AND (:exclude_id IS NULL OR o.operator_id <> :exclude_id)
                """,
                {
                    "short_name": short_name,
                    "operator_type": operator_type,
                    "exclude_id": exclude_id,
                },
            ).fetchone()
        return dict(row._mapping) if row else None

    class UpdateOperatorFieldResult(TypedDict):
        success: bool
        error: NotRequired[str]

    @classmethod
    def update_operator_field(
        cls,
        operator_id: int,
        field: Literal["short_name", "long_name", "operator_type"],
        value: str,
    ) -> UpdateOperatorFieldResult:
        if (field == "short_name" or field == "long_name") and len(value) == 0:
            return {
                "success": False,
                "error": "short_name and long_name cannot be empty.",
            }
        if field == "operator_type" and value not in OPERATOR_TYPES:
            return {"success": False, "error": "invalid operator_type"}

        with pg_session() as pg:
            operator_type = pg.execute(
                "SELECT operator_type FROM operators WHERE operator_id = :id",
                {"id": operator_id},
            ).scalar()

            if field in ("short_name", "long_name"):
                # Reject a name already resolving to a different operator, before
                # writing anything.
                taken = pg.execute(
                    """
                    SELECT o.short_name FROM operator_aliases a
                    JOIN operators o ON o.operator_id = a.operator_id
                    WHERE a.operator_type = :type
                      AND a.normalized = operator_normalize(:value)
                      AND a.operator_id <> :id
                    """,
                    {"type": operator_type, "value": value, "id": operator_id},
                ).fetchone()
                if taken is not None:
                    return {
                        "success": False,
                        "error": f"'{value}' already resolves to {taken['short_name']}",
                    }

            pg.execute(
                f"UPDATE operators SET {field} = :value WHERE operator_id = :operator_id",
                {"value": value, "operator_id": operator_id},
            )

            if field in ("short_name", "long_name"):
                # The operator's own names are alias rows — that is how trips find it.
                # Reconcile both from the current values rather than editing the one
                # field's alias: when short and long share a spelling they share a
                # single alias row, so editing one name in isolation could delete the
                # spelling the *other* still uses. (This is the bug that stranded
                # trips logged "České dráhy" when its short name changed to "ČD".)
                cls._reconcile_name_aliases(pg, operator_id, operator_type)
                resync_operator_aliases(operator_id, pg_session_=pg)
            return {"success": True}

    @staticmethod
    def _reconcile_name_aliases(pg, operator_id, operator_type):
        """Make the operator's short/long alias rows match its current names.

        Drops any short/long alias no longer equal to either name, then ensures a
        'short' alias for short_name and a 'long' alias for long_name. Admin-added
        'alias' rows are never touched. When the two names normalise alike only one
        row can exist (the unique index), and it is kept as 'short'.
        """
        names = pg.execute(
            "SELECT short_name, long_name FROM operators WHERE operator_id = :id",
            {"id": operator_id},
        ).fetchone()

        pg.execute(
            """
            DELETE FROM operator_aliases
            WHERE operator_id = :id AND kind IN ('short', 'long')
              AND normalized NOT IN (
                  operator_normalize(:short_name), operator_normalize(:long_name)
              )
            """,
            {"id": operator_id, "short_name": names["short_name"], "long_name": names["long_name"]},
        )

        # Short first, so if short and long collide the surviving row is 'short'.
        pg.execute(
            "INSERT INTO operator_aliases (operator_id, operator_type, alias, kind)"
            " VALUES (:id, :type, :alias, 'short') ON CONFLICT DO NOTHING",
            {"id": operator_id, "type": operator_type, "alias": names["short_name"]},
        )
        long_is_distinct = pg.execute(
            "SELECT operator_normalize(:short_name) IS DISTINCT FROM operator_normalize(:long_name)"
            " AND operator_normalize(:long_name) IS NOT NULL",
            {"short_name": names["short_name"], "long_name": names["long_name"]},
        ).scalar()
        if long_is_distinct:
            # An old row for this spelling may survive under the wrong kind (it was
            # the short name before the rename); realign it to 'long'.
            pg.execute(
                """
                UPDATE operator_aliases SET kind = 'long'
                WHERE operator_id = :id AND kind = 'short'
                  AND normalized = operator_normalize(:long_name)
                """,
                {"id": operator_id, "long_name": names["long_name"]},
            )
            pg.execute(
                "INSERT INTO operator_aliases (operator_id, operator_type, alias, kind)"
                " VALUES (:id, :type, :alias, 'long') ON CONFLICT DO NOTHING",
                {"id": operator_id, "type": operator_type, "alias": names["long_name"]},
            )

    @classmethod
    def delete(cls, operator_id: int):
        with pg_session() as pg:
            pg.execute(
                "DELETE FROM operator_logos WHERE operator_id = :operator_id",
                {"operator_id": operator_id},
            )
            pg.execute(
                "DELETE FROM operators WHERE operator_id = :operator_id",
                {"operator_id": operator_id},
            )
        # Remove logo files for this operator.
        for file in os.listdir(LOGO_UPLOAD_FOLDER):
            if file.startswith(f"{operator_id}_"):
                logo_path = os.path.join(LOGO_UPLOAD_FOLDER, file)
                if os.path.exists(logo_path):
                    os.remove(logo_path)

    @classmethod
    def get_aliases(cls, operator_id: int):
        """Every spelling that resolves to this operator, canonical names included."""
        with pg_session() as pg:
            result = pg.execute(
                """
                SELECT alias_id, alias, normalized, kind, lang
                FROM operator_aliases
                WHERE operator_id = :operator_id
                ORDER BY (kind = 'short') DESC, (kind = 'long') DESC, alias
                """,
                {"operator_id": operator_id},
            )
            columns = ["alias_id", "alias", "normalized", "kind", "lang"]
            return [dict(zip(columns, row)) for row in result.fetchall()]

    @classmethod
    def find_alias_conflict(cls, operator_id: int, alias: str):
        """The operator already owning this spelling in the same pool, if any.

        A spelling means exactly one operator per pool (the unique index enforces it),
        so the admin has to merge rather than alias when it is already taken.
        """
        with pg_session() as pg:
            row = pg.execute(
                """
                SELECT o.operator_id, o.short_name, o.long_name, a.alias
                FROM operator_aliases a
                JOIN operators o ON o.operator_id = a.operator_id
                WHERE a.normalized = operator_normalize(:alias)
                  AND a.operator_type = (
                      SELECT operator_type FROM operators WHERE operator_id = :operator_id
                  )
                """,
                {"alias": alias, "operator_id": operator_id},
            ).fetchone()
        if row is None:
            return None
        return dict(row._mapping)

    @classmethod
    def add_alias(cls, operator_id: int, alias: str, lang: str | None = None):
        """Record another spelling for this operator and re-resolve affected trips."""
        with pg_session() as pg:
            row = pg.execute(
                """
                INSERT INTO operator_aliases (operator_id, operator_type, alias, lang, kind)
                SELECT :operator_id, o.operator_type, :alias, :lang, 'alias'
                FROM operators o WHERE o.operator_id = :operator_id
                RETURNING alias_id, alias, normalized, kind, lang
                """,
                {"operator_id": operator_id, "alias": alias, "lang": lang},
            ).fetchone()
            # Trips spelling it this way resolve to this operator from now on.
            resynced = resync_operator_aliases(operator_id, pg_session_=pg)
        result = dict(row._mapping)
        result["trips_resynced"] = resynced
        return result

    @classmethod
    def delete_alias(cls, alias_id: int):
        """Drop a spelling. Trips using it fall back to their own text."""
        with pg_session() as pg:
            row = pg.execute(
                "DELETE FROM operator_aliases WHERE alias_id = :alias_id"
                " RETURNING operator_id, alias",
                {"alias_id": alias_id},
            ).fetchone()
            if row is None:
                return None
            resynced = resync_operator_aliases(row["operator_id"], pg_session_=pg)
        return {"operator_id": row["operator_id"], "trips_resynced": resynced}

    @classmethod
    def merge(cls, source_id: int, target_id: int):
        """Fold one operator into another.

        Needed because duplicates already exist as separate rows (SBB/CFF/FFS), and
        the unique index means a spelling cannot simply be aliased onto a second
        operator while the first still owns it.

        The source's logos and aliases move to the target, so no spelling and no
        artwork is lost, then the source row goes away and its trips re-resolve.
        """
        with pg_session() as pg:
            same_pool = pg.execute(
                "SELECT (SELECT operator_type FROM operators WHERE operator_id = :s)"
                " = (SELECT operator_type FROM operators WHERE operator_id = :t)",
                {"s": source_id, "t": target_id},
            ).scalar()
            if not same_pool:
                return {"success": False, "error": "operators are in different pools"}

            # Collect the affected trips up front: deleting the source sets its
            # trip_operators rows to NULL (ON DELETE SET NULL), so afterwards they no
            # longer point at either operator and could not be found to re-resolve.
            affected = [
                row[0]
                for row in pg.execute(
                    "SELECT DISTINCT trip_id FROM trip_operators"
                    " WHERE operator_id IN (:s, :t)",
                    {"s": source_id, "t": target_id},
                ).fetchall()
            ]

            pg.execute(
                "UPDATE operator_logos SET operator_id = :t WHERE operator_id = :s",
                {"s": source_id, "t": target_id},
            )
            # Move the source's spellings across, dropping any the target already
            # owns; they become plain aliases, since the target keeps its own names.
            pg.execute(
                """
                UPDATE operator_aliases a SET operator_id = :t, kind = 'alias'
                WHERE a.operator_id = :s
                  AND NOT EXISTS (
                      SELECT 1 FROM operator_aliases b
                      WHERE b.operator_id = :t AND b.normalized = a.normalized
                  )
                """,
                {"s": source_id, "t": target_id},
            )
            pg.execute(
                "DELETE FROM operator_aliases WHERE operator_id = :s", {"s": source_id}
            )
            pg.execute(
                "DELETE FROM operators WHERE operator_id = :s", {"s": source_id}
            )
            sync_trip_operators(affected, pg_session_=pg)
        return {"success": True, "trips_resynced": len(affected)}

    @classmethod
    def get_group_members(cls, operator_id: int):
        """The operators counted as one with this one, itself included.

        Empty when it is not grouped.
        """
        with pg_session() as pg:
            rows = pg.execute(
                """
                SELECT m.operator_id, m.short_name, m.long_name, m.operator_type
                FROM operators o
                JOIN operators m ON m.group_id = o.group_id
                WHERE o.operator_id = :operator_id AND o.group_id IS NOT NULL
                ORDER BY m.short_name
                """,
                {"operator_id": operator_id},
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    @classmethod
    def link_operators(cls, operator_id: int, other_id: int):
        """Put two operators in the same equivalence group.

        Unlike a merge, both keep their name, their logo and their own trips; only
        the stats add them together. Nothing needs re-resolving, because
        trip_operators still records the specific operator each trip named.

        If either already belongs to a group, the two groups are combined, so linking
        is transitive: SBB-CFF then CFF-FFS gives one group of three.
        """
        with pg_session() as pg:
            rows = pg.execute(
                "SELECT operator_id, operator_type, group_id FROM operators"
                " WHERE operator_id IN (:a, :b)",
                {"a": operator_id, "b": other_id},
            ).fetchall()
            if len(rows) != 2:
                return {"success": False, "error": "operator not found"}
            first, second = rows
            if first["operator_type"] != second["operator_type"]:
                return {"success": False, "error": "operators are in different pools"}

            groups = [r["group_id"] for r in rows if r["group_id"] is not None]
            if not groups:
                group_id = pg.execute(
                    "INSERT INTO operator_groups (note) VALUES (NULL) RETURNING group_id"
                ).scalar()
            else:
                # Keep the lowest existing group and absorb the other one, so linking
                # two already-grouped operators unites both memberships.
                group_id = min(groups)
                for other_group in set(groups) - {group_id}:
                    pg.execute(
                        "UPDATE operators SET group_id = :keep WHERE group_id = :drop",
                        {"keep": group_id, "drop": other_group},
                    )
                    pg.execute(
                        "DELETE FROM operator_groups WHERE group_id = :drop",
                        {"drop": other_group},
                    )

            pg.execute(
                "UPDATE operators SET group_id = :g WHERE operator_id IN (:a, :b)",
                {"g": group_id, "a": operator_id, "b": other_id},
            )
            members = pg.execute(
                "SELECT count(*) FROM operators WHERE group_id = :g", {"g": group_id}
            ).scalar()
        return {"success": True, "group_id": group_id, "members": members}

    @classmethod
    def unlink_operator(cls, operator_id: int):
        """Take one operator out of its group; it counts on its own again."""
        with pg_session() as pg:
            group_id = pg.execute(
                "SELECT group_id FROM operators WHERE operator_id = :operator_id",
                {"operator_id": operator_id},
            ).scalar()
            pg.execute(
                "UPDATE operators SET group_id = NULL WHERE operator_id = :operator_id",
                {"operator_id": operator_id},
            )
            # A group of one is not a group.
            if group_id is not None:
                remaining = pg.execute(
                    "SELECT count(*) FROM operators WHERE group_id = :g", {"g": group_id}
                ).scalar()
                if remaining < 2:
                    pg.execute(
                        "UPDATE operators SET group_id = NULL WHERE group_id = :g",
                        {"g": group_id},
                    )
                    pg.execute(
                        "DELETE FROM operator_groups WHERE group_id = :g", {"g": group_id}
                    )
        return {"success": True}

    @classmethod
    def get_operator_logos(cls, operator_id: int):
        with pg_session() as pg:
            result = pg.execute(
                "SELECT uid, operator_id, logo_url, effective_date FROM operator_logos WHERE operator_id = :operator_id",
                {"operator_id": operator_id},
            )
            columns = ["uid", "operator_id", "logo_url", "effective_date"]
            return [dict(zip(columns, row)) for row in result.fetchall()]

    @classmethod
    def add_operator_logo(
        cls, operator_id: int, logo_url: str, effective_date: datetime | None
    ):
        with pg_session() as pg:
            result = pg.execute(
                """
                INSERT INTO operator_logos (operator_id, logo_url, effective_date)
                VALUES (:operator_id, :logo_url, :effective_date)
                RETURNING uid, operator_id, logo_url, effective_date""",
                {
                    "operator_id": operator_id,
                    "logo_url": logo_url,
                    "effective_date": effective_date if effective_date else None,
                },
            )
            columns = ["uid", "operator_id", "logo_url", "effective_date"]
            return dict(zip(columns, result.fetchone()))

    @classmethod
    def delete_operator_logo(cls, logo_id: int):
        with pg_session() as pg:
            pg.execute(
                "DELETE FROM operator_logos WHERE uid = :logo_id", {"logo_id": logo_id}
            )
