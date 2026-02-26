import json

from flask import jsonify, request, render_template, Blueprint, session, redirect, url_for

from src.pg import pg_session
from src.utils import has_current_trip, lang

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
    """Autocomplete search across nom, titre1, titre2, notes."""
    username, _ = _session_user()
    if not username:
        return jsonify({'error': 'Unauthorized'}), 401

    q     = request.args.get('q', '').strip()
    limit = min(int(request.args.get('limit', 20)), 100)
    if not q:
        return jsonify([])

    like   = f'%{q}%'
    starts = f'{q}%'

    with pg_session() as pg:
        result = pg.execute(
            """
            SELECT source, titre1, titre2, nom, epo, datmaj, image, name, notes, typeligne
            FROM wagons
            WHERE nom ILIKE :like OR titre1 ILIKE :like OR titre2 ILIKE :like OR notes ILIKE :like
            ORDER BY
                CASE WHEN nom ILIKE :starts THEN 0 ELSE 1 END,
                nom
            LIMIT :limit
            """,
            {"like": like, "starts": starts, "limit": limit},
        )
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
            "SELECT username, is_admin::int AS is_admin FROM trainsets WHERE id = :id",
            {"id": tid},
        )
        row = result.fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        if row['is_admin'] and not is_admin:
            return jsonify({'error': 'Only admins can edit public trainsets'}), 403
        if not row['is_admin'] and row['username'] != username:
            return jsonify({'error': 'Forbidden'}), 403
        pg.execute(
            """
            UPDATE trainsets
            SET name = :name, units_json = :units_json, updated_at = NOW()
            WHERE id = :id
            """,
            {"name": name, "units_json": json.dumps(units), "id": tid},
        )

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


# ── helpers ──────────────────────────────────────────────────────────────────

def _slim_units(units):
    """Keep only wagon name and flip-side — all other data lives in the wagons table."""
    return [{'name': u['name'], '_side': u.get('_side', 'L')} for u in units if 'name' in u]


def _enrich_units(pg, slim_units):
    """Join slim unit refs with the wagons table to restore display fields."""
    enriched = []
    for u in slim_units:
        result = pg.execute(
            "SELECT titre1, titre2, nom, epo, image, name, notes FROM wagons WHERE name = :name",
            {"name": u['name']},
        )
        wagon = result.fetchone()
        if wagon:
            unit          = dict(wagon)
            unit['_side'] = u.get('_side', 'L')
            enriched.append(unit)
    return enriched
