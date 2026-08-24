import json
import logging
import shlex
import time
from collections import Counter

from flask import Blueprint, jsonify, request

from src.api.trainset import _enrich_units
from src.pg import pg_session
from src.users import User
from src.utils import admin_required

logger = logging.getLogger(__name__)

trainsets_admin_blueprint = Blueprint("admin_trainsets", __name__)

_COL_MAP = {0: "name", 2: "username", 3: "cars", 4: "uses", 5: "users", 6: "created_at"}

_USAGE_TTL = 300          # seconds; the admin table re-queries on every page change
_usage_cache: dict = {"at": 0.0, "counts": {}, "users": {}}


def _uid_to_username() -> dict[int, str]:
    """The auth DB is a separate SQLite database from the trips/trainsets Postgres
    one, so trips.user_id can't be joined to it in SQL — resolve it in Python."""
    return {u.uid: u.username for u in User.query.with_entities(User.uid, User.username)}


def _trainset_usage() -> tuple[dict[int, int], dict[int, int]]:
    """
    Trips using each trainset, keyed by trainset id, plus how many distinct users
    those trips belong to.

    Mirrors _wagon_usage() in wagons.py, one level up: trips.material_type_advanced
    holds either an inline JSON array of units (a one-off composition, not a saved
    trainset — skipped here), a composite {"trainsets": [names]}, or a bare trainset
    name. A name alone is ambiguous when several trainsets share it — a personal set
    and a public one, or two different users' personal sets — so a bare/composite name
    is resolved against the trip owner's own personal trainset first, falling back to
    a public (is_admin) one of that name.
    """
    now = time.time()
    if now - _usage_cache["at"] < _USAGE_TTL:
        return _usage_cache["counts"], _usage_cache["users"]

    counts: Counter = Counter()
    user_sets: dict[int, set] = {}
    try:
        with pg_session() as pg:
            trainset_rows = pg.execute(
                "SELECT id, name, username, is_admin FROM trainsets"
            ).fetchall()
            rows = pg.execute(
                """
                SELECT material_type_advanced AS mta, user_id, COUNT(*) AS n
                FROM trips
                WHERE material_type_advanced IS NOT NULL AND material_type_advanced <> ''
                GROUP BY material_type_advanced, user_id
                """
            ).fetchall()

        public_by_name: dict[str, list[int]] = {}
        personal_by_user_name: dict[tuple[str, str], int] = {}
        for r in trainset_rows:
            if r["is_admin"]:
                public_by_name.setdefault(r["name"], []).append(r["id"])
            elif r["username"]:
                personal_by_user_name[(r["username"], r["name"])] = r["id"]

        uid_to_username = _uid_to_username()

        def _resolve(name: str, username: str | None) -> list[int]:
            if username:
                pid = personal_by_user_name.get((username, name))
                if pid is not None:
                    return [pid]
            return public_by_name.get(name, [])

        for row in rows:
            value, user_id, n = (row["mta"] or "").strip(), row["user_id"], row["n"]
            if not value or value.startswith("["):
                continue  # an inline composition, not a reference to a saved trainset

            names: list[str] = []
            if value.startswith("{"):
                try:
                    names = [x for x in (json.loads(value).get("trainsets") or []) if x]
                except (TypeError, ValueError, AttributeError):
                    continue
            else:
                names = [value]

            username = uid_to_username.get(user_id)
            for name in names:
                for tid in _resolve(name, username):
                    counts[tid] += n
                    user_sets.setdefault(tid, set()).add(user_id)
    except Exception as e:                        # never break the listing over a stat
        logger.warning("trainset usage count failed: %s", e)
        return _usage_cache["counts"], _usage_cache["users"]

    users = {t: len(s) for t, s in user_sets.items()}
    _usage_cache.update(at=now, counts=dict(counts), users=users)
    return _usage_cache["counts"], _usage_cache["users"]


@trainsets_admin_blueprint.route("", methods=["GET"])
@admin_required
def list_trainsets():
    draw   = request.args.get("draw",   1,   type=int)
    start  = request.args.get("start",  0,   type=int)
    length = request.args.get("length", 25,  type=int)
    search = request.args.get("search[value]", "").strip()

    order_col = _COL_MAP.get(request.args.get("order[0][column]", 0, type=int), "uses")
    order_dir = "ASC" if request.args.get("order[0][dir]", "asc") == "asc" else "DESC"
    # The catalogue is dominated by one-off personal sets, which drown out the curated
    # public ones — default to public-only, with the client offering an opt-in toggle.
    only_public = request.args.get("only_public", "1") != "0"

    # Must run before the session below opens — pg_session() refuses to nest.
    usage, usage_users = _trainset_usage()

    with pg_session() as pg:
        total = pg.execute("SELECT COUNT(*) FROM trainsets").scalar()

        qparams = {"limit": length, "offset": start}
        clauses = []
        if only_public:
            clauses.append("is_admin")
        if search:
            try:
                terms = [t.strip() for t in shlex.split(search) if t.strip()]
            except ValueError:
                terms = search.split()
            _field_exprs = ["name ILIKE :t{i}", "username ILIKE :t{i}"]
            for i, term in enumerate(terms):
                qparams[f"t{i}"] = f"%{term}%"
                exprs = [e.format(i=i) for e in _field_exprs]
                clauses.append("(" + " OR ".join(exprs) + ")")

        where    = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        filtered = pg.execute(
            f"SELECT COUNT(*) FROM trainsets {where}", qparams
        ).scalar()

        # Usage counts live in Python (see _trainset_usage), fed back in as parallel
        # arrays and joined — keeps sorting/paging by popularity in SQL. "cars" is the
        # one column SQL can't sort on directly, since it's the length of units_json
        # (parsed leniently in Python below, same as _wagon_usage does) rather than a
        # real column — that one case falls back to a full fetch + Python sort/page.
        qparams["u_ids"]    = list(usage.keys())
        qparams["u_counts"] = list(usage.values())
        qparams["u_users"]  = [usage_users.get(i, 0) for i in usage]

        base_query = f"""
            SELECT id, name, username, is_admin, units_json, created_at,
                   COALESCE(u.ucount, 0) AS uses,
                   COALESCE(u.ausers, 0) AS users
            FROM trainsets
            LEFT JOIN unnest(CAST(:u_ids AS int[]), CAST(:u_counts AS bigint[]),
                             CAST(:u_users AS bigint[]))
                   AS u(uid, ucount, ausers) ON u.uid = trainsets.id
            {where}
        """

        if order_col == "cars":
            rows = pg.execute(base_query, qparams).fetchall()
        else:
            qparams["limit"], qparams["offset"] = length, start
            rows = pg.execute(
                base_query + f" ORDER BY {order_col} {order_dir} NULLS LAST "
                             "LIMIT :limit OFFSET :offset",
                qparams,
            ).fetchall()

    data = []
    for r in rows:
        row = dict(r)
        try:
            row["cars"] = len(json.loads(row.pop("units_json") or "[]"))
        except (TypeError, ValueError):
            row["cars"] = 0
        row["created_at"] = row["created_at"].isoformat() if row["created_at"] else None
        data.append(row)

    if order_col == "cars":
        data.sort(key=lambda r: r["cars"], reverse=(order_dir == "DESC"))
        data = data[start:start + length]

    return jsonify({"draw": draw, "recordsTotal": total,
                    "recordsFiltered": filtered, "data": data})


@trainsets_admin_blueprint.route("<int:tid>/units", methods=["GET"])
@admin_required
def trainset_units(tid):
    """Enriched units for one trainset, for the admin table's design preview.

    Unlike GET /api/trainsets/<id>, this isn't scoped to the viewing admin's own
    visibility — an admin moderating the catalogue needs to preview any trainset,
    public or personal, regardless of who owns it.
    """
    with pg_session() as pg:
        row = pg.execute(
            "SELECT units_json FROM trainsets WHERE id = :id", {"id": tid}
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        units = _enrich_units(pg, json.loads(row["units_json"] or "[]"))

    return jsonify({"units": units})
