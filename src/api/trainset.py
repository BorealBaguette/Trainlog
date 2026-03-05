import json
import shlex

from flask import jsonify, request, render_template, Blueprint, session, redirect, url_for

from src.pg import pg_session
from src.utils import has_current_trip, lang, mainConn, managed_cursor, get_user_id

trainset_blueprint = Blueprint('trainset', __name__)


def _session_user():
    """Return (username, is_admin) from the current session."""
    username = session.get("logged_in")
    is_admin = bool(session.get("userinfo", {}).get("is_admin", False))
    return username, is_admin


@trainset_blueprint.route('/trainset-builder')
def trainset_builder():
    username, is_admin = _session_user()
    if not username:
        return redirect(url_for("login", next=request.path))
    return render_template(
        'trainset.html',
        nav="bootstrap/navigation.html",
        username=username,
        title="Trainset Builder",
        isCurrent=has_current_trip(),
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


@trainset_blueprint.route('/api/wagons/search')
def search_wagons():
    """Autocomplete search across nom, titre1, titre2, notes.
    Supports multi-term AND search across fields. Quoted phrases are kept together.
    """
    username, _ = _session_user()
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    q = request.args.get('q', '').strip()
    try:
        limit = min(int(request.args.get('limit', 20)), 100)
    except (TypeError, ValueError):
        limit = 20

    if not q:
        return jsonify([])

    # Terms: supports quotes, e.g. RRR "Grand Est"
    try:
        terms = [t.strip() for t in shlex.split(q) if t.strip()]
    except ValueError:
        # Fallback if user typed unmatched quotes
        terms = [t for t in q.split() if t]

    if not terms:
        return jsonify([])

    # Build: AND over terms, OR over fields
    fields = ["nom", "titre1", "titre2", "notes"]
    params = {"limit": limit, "q_like": f"%{q}%"}  # for ranking only
    where_parts = []

    for i, term in enumerate(terms):
        key = f"t{i}"
        params[key] = f"%{term}%"
        where_parts.append("(" + " OR ".join([f"{f} ILIKE :{key}" for f in fields]) + ")")

    where_sql = " AND ".join(where_parts)

    # Ranking: boost exact/starts-with on nom for the full query (nice for autocomplete)
    # You can add more ranking rules if you want.
    params["q_starts"] = f"{q}%"

    sql = f"""
        SELECT source, titre1, titre2, nom, epo, datmaj, image, name, notes, typeligne, image_type
        FROM wagons
        WHERE {where_sql}
        ORDER BY
            CASE WHEN nom ILIKE :q_starts THEN 0 ELSE 1 END,
            CASE WHEN nom ILIKE :q_like THEN 0 ELSE 1 END,
            nom
        LIMIT :limit
    """

    with pg_session() as pg:
        result = pg.execute(sql, params)
        rows = [dict(r) for r in result]

    return jsonify(rows)


@trainset_blueprint.route('/api/trainsets', methods=['GET'])
def list_trainsets():
    """List trainsets visible to the current user:
       - public (is_admin=true) sets are visible to everyone
       - personal (is_admin=false) sets are only visible to their creator
    """
    username, _ = _session_user()
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    with pg_session() as pg:
        result = pg.execute(
            """
            SELECT id, name, username, is_admin::int AS is_admin, created_at, updated_at
            FROM trainsets
            WHERE is_admin OR username = :username
            ORDER BY is_admin DESC, updated_at DESC
            """,
            {"username": username},
        )
        rows = [dict(r) for r in result]

    return jsonify(rows)


@trainset_blueprint.route('/api/trainsets', methods=['POST'])
def create_trainset():
    """Create a new trainset. Only admins may create public (is_admin=true) sets."""
    username, is_admin = _session_user()
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    data            = request.get_json()
    name            = data.get('name', 'Unnamed trainset').strip()
    requested_admin = bool(data.get('is_admin'))
    set_is_admin    = requested_admin and is_admin
    units           = _slim_units(data.get('units', []))

    with pg_session() as pg:
        result = pg.execute(
            """
            INSERT INTO trainsets (name, username, is_admin, units_json)
            VALUES (:name, :username, :is_admin, :units_json)
            RETURNING id
            """,
            {
                "name":       name,
                "username":   username,
                "is_admin":   set_is_admin,
                "units_json": json.dumps(units),
            },
        )
        trainset_id = result.scalar()

    return jsonify({'id': trainset_id, 'name': name, 'is_admin': int(set_is_admin)}), 201


@trainset_blueprint.route('/api/trainsets/by-name')
def get_trainset_by_name():
    username, _ = _session_user()
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400

    with pg_session() as pg:
        result = pg.execute(
            """
            SELECT id, name, username, is_admin::int AS is_admin,
                   created_at, updated_at, units_json
            FROM trainsets WHERE name = :name
              AND (is_admin OR username = :username)
            """,
            {"name": name, "username": username},
        )
        row = result.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        d = dict(row)
        slim_units = json.loads(d.pop('units_json') or '[]')
        d['units'] = _enrich_units(pg, slim_units)

    return jsonify(d)


@trainset_blueprint.route('/api/trainsets/<int:tid>', methods=['GET'])
def get_trainset(tid):
    username, _ = _session_user()
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    with pg_session() as pg:
        result = pg.execute(
            """
            SELECT id, name, username, is_admin::int AS is_admin,
                   created_at, updated_at, units_json
            FROM trainsets WHERE id = :id
            """,
            {"id": tid},
        )
        row = result.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        d = dict(row)
        if not d['is_admin'] and d['username'] != username:
            return jsonify({'error': 'Forbidden'}), 403
        slim_units = json.loads(d.pop('units_json') or '[]')
        d['units'] = _enrich_units(pg, slim_units)

    return jsonify(d)


@trainset_blueprint.route('/api/trainsets/<int:tid>', methods=['PUT'])
def update_trainset(tid):
    """Update name and units. Only the owner may edit personal sets;
       only admins may edit public sets."""
    username, is_admin = _session_user()
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    data  = request.get_json()
    name  = data.get('name', 'Unnamed').strip()
    units = _slim_units(data.get('units', []))

    with pg_session() as pg:
        result = pg.execute(
            "SELECT username, is_admin::int AS is_admin, name AS old_name FROM trainsets WHERE id = :id",
            {"id": tid},
        )
        row = result.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        if row['is_admin'] and not is_admin:
            return jsonify({'error': 'Only admins can edit public trainsets'}), 403
        if not row['is_admin'] and row['username'] != username:
            return jsonify({'error': 'Forbidden'}), 403
        old_name = row['old_name']
        pg.execute(
            """
            UPDATE trainsets
            SET name = :name, units_json = :units_json, updated_at = NOW()
            WHERE id = :id
            """,
            {"name": name, "units_json": json.dumps(units), "id": tid},
        )
        if name != old_name:
            if row['is_admin']:
                pg.execute(
                    "UPDATE trips SET material_type_advanced = :new WHERE material_type_advanced = :old",
                    {"new": name, "old": old_name},
                )
                with managed_cursor(mainConn) as cursor:
                    cursor.execute(
                        "UPDATE trip SET material_type_advanced = :new WHERE material_type_advanced = :old",
                        {"new": name, "old": old_name},
                    )
            else:
                pg.execute(
                    "UPDATE trips SET material_type_advanced = :new WHERE user_id = :user AND material_type_advanced = :old",
                    {"new": name, "old": old_name, "user": get_user_id(username)},
                )
                with managed_cursor(mainConn) as cursor:
                    cursor.execute(
                        "UPDATE trip SET material_type_advanced = :new WHERE username = :user AND material_type_advanced = :old",
                        {"new": name, "old": old_name, "user": username},
                    )
            mainConn.commit()

    return jsonify({'id': tid, 'name': name})


@trainset_blueprint.route('/api/trainsets/<int:tid>', methods=['DELETE'])
def delete_trainset(tid):
    """Delete a trainset. Same ownership rules as update."""
    username, is_admin = _session_user()
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    with pg_session() as pg:
        result = pg.execute(
            "SELECT username, is_admin::int AS is_admin FROM trainsets WHERE id = :id",
            {"id": tid},
        )
        row = result.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        if row['is_admin'] and not is_admin:
            return jsonify({'error': 'Only admins can delete public trainsets'}), 403
        if not row['is_admin'] and row['username'] != username:
            return jsonify({'error': 'Forbidden'}), 403
        pg.execute("DELETE FROM trainsets WHERE id = :id", {"id": tid})

    return jsonify({'deleted': tid})


@trainset_blueprint.route('/api/trainsets/resolve')
def resolve_material_type_advanced():
    """Resolve a material_type_advanced value to enriched units.

    Accepts a `value` query param that is either:
    - A JSON array of slim units (from "use once") → enriched directly
    - A trainset name (from "save & use") → looked up by name
    """
    username, _ = _session_user()
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    value = request.args.get('value', '').strip()
    if not value:
        return jsonify([])

    with pg_session() as pg:
        # Try JSON array first
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                slim = [{'name': u['name'], '_side': u.get('_side', 'L')} for u in parsed if 'name' in u]
                return jsonify(_enrich_units(pg, slim))
        except (json.JSONDecodeError, TypeError):
            pass

        # Fall back to trainset name lookup
        result = pg.execute(
            """
            SELECT units_json FROM trainsets
            WHERE name = :name AND (is_admin OR username = :username)
            """,
            {"name": value, "username": username},
        )
        row = result.fetchone()
        if not row:
            return jsonify([])
        slim_units = json.loads(row['units_json'] or '[]')
        return jsonify(_enrich_units(pg, slim_units))


# ── helpers ──────────────────────────────────────────────────────────────────

def _slim_units(units):
    """Keep only wagon name and flip-side — all other data lives in the wagons table."""
    return [{'name': u['name'], '_side': u.get('_side', 'L')} for u in units if 'name' in u]


def _enrich_units(pg, slim_units):
    """Join slim unit refs with the wagons table to restore display fields."""
    enriched = []
    for u in slim_units:
        result = pg.execute(
            "SELECT titre1, titre2, nom, epo, image, name, notes, image_type FROM wagons WHERE name = :name",
            {"name": u['name']},
        )
        wagon = result.fetchone()
        if wagon:
            unit          = dict(wagon)
            unit['_side'] = u.get('_side', 'L')
            enriched.append(unit)
    return enriched
