import json
import shlex

from flask import jsonify, request, render_template, Blueprint, session, redirect, url_for

from src.pg import pg_session
from src.utils import has_current_trip, lang, get_user_id

trainset_blueprint = Blueprint('trainset', __name__)


def _session_user():
    """Return (username, is_admin) from the current session."""
    username = session.get("logged_in")
    is_admin = bool(session.get("userinfo", {}).get("is_admin", False))
    return username, is_admin


def _view_as_username():
    """Admin debug helper: an admin may pass ?as=<username> to browse the
    trainset builder exactly as that user sees it (their personal sets plus
    public sets), read-only. Returns the username whose visibility applies.

    Only honoured for admins; for anyone else it collapses to their own
    session, so the param can never widen a non-admin's visibility.
    """
    username, is_admin = _session_user()
    if is_admin:
        as_user = request.args.get('as', '').strip()
        if as_user:
            return as_user
    return username or ''


def _visibility_username():
    """Username to scope trainset visibility to for read-only GET endpoints.

    Visibility for a trainset follows the trip page it's being shown on, not
    who's viewing it — the same rule public_trainset_info already applies for
    the single-trip public page (it resolves against the trip owner, so a
    personal trainset the owner built still renders on their own public trip
    for any visitor, logged in or not). So an explicit `owner` query param
    (the page/trip owner) always wins when given, whether the caller is
    logged in or anonymous. Only when no owner is supplied (e.g. the
    trainset-builder tools) do we scope to the caller's own session.
    """
    owner = request.args.get('owner', '').strip()
    if owner:
        return owner
    username, _ = _session_user()
    return username or ''


@trainset_blueprint.route('/trainset-builder')
def trainset_builder():
    username, is_admin = _session_user()
    if not username:
        return redirect(url_for("login", next=request.path))
    # Admin debug: ?as=<username> renders the builder read-only from that
    # user's perspective (their personal sets + public sets).
    view_as = request.args.get('as', '').strip() if is_admin else ''
    return render_template(
        'trainset.html',
        nav="bootstrap/navigation.html",
        username=username,
        view_as=view_as,
        title="Trainset Builder",
        isCurrent=has_current_trip(),
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


@trainset_blueprint.route('/api/wagons/search')
def search_wagons():
    """Autocomplete search across label, category, subcategory, notes.
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
    text_fields = ["label", "category", "subcategory", "notes", "era", "source", "author", "license", "name"]
    params = {"limit": limit, "q_like": f"%{q}%"}  # for ranking only
    where_parts = []

    for i, term in enumerate(terms):
        key = f"t{i}"
        params[key] = f"%{term}%"
        exprs = [f"{f} ILIKE :{key}" for f in text_fields] + [f"gauge::text ILIKE :{key}"]
        where_parts.append("(" + " OR ".join(exprs) + ")")

    where_sql = " AND ".join(where_parts)

    # Ranking: boost exact/starts-with on nom for the full query (nice for autocomplete)
    # You can add more ranking rules if you want.
    params["q_starts"] = f"{q}%"

    sql = f"""
        SELECT source, category, subcategory, label, era, updated_on, image, name, notes, line_type, image_type, image_ext, px_per_meter
        FROM wagons
        WHERE {where_sql}
        ORDER BY
            CASE WHEN label ILIKE :q_starts THEN 0 ELSE 1 END,
            CASE WHEN label ILIKE :q_like THEN 0 ELSE 1 END,
            label
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

    Admins may pass ?as=<username> to list what that user would see.
    """
    username = _view_as_username()

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
        if _name_taken(pg, name, username, set_is_admin):
            return jsonify({'error': _dup_name_error(name, set_is_admin)}), 409
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
    username = _visibility_username()

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
            ORDER BY (username = :username AND NOT is_admin) DESC
            LIMIT 1
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

    # Admin ?as=<username> resolves visibility to that user (read-only debug view).
    visible_to = _view_as_username()

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
        if not d['is_admin'] and d['username'] != visible_to:
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
        if _name_taken(pg, name, row['username'], bool(row['is_admin']), exclude_id=tid):
            return jsonify({'error': _dup_name_error(name, bool(row['is_admin']))}), 409
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
                _rename_in_composites(pg, old_name, name)
            else:
                user_id = get_user_id(username)
                pg.execute(
                    "UPDATE trips SET material_type_advanced = :new WHERE user_id = :user AND material_type_advanced = :old",
                    {"new": name, "old": old_name, "user": user_id},
                )
                _rename_in_composites(pg, old_name, name, user_id)

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
    username = _visibility_username()

    value = request.args.get('value', '').strip()
    if not value:
        return jsonify([])

    with pg_session() as pg:
        # Try JSON first: array of slim units, or composite {"trainsets": [names]}
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                slim = _slim_units(parsed)
                return jsonify(_enrich_units(pg, slim))
            if isinstance(parsed, dict) and isinstance(parsed.get('trainsets'), list):
                units = []
                for name in parsed['trainsets']:
                    units.extend(_units_by_name(pg, name, username))
                return jsonify(units)
        except (json.JSONDecodeError, TypeError):
            pass

        # Fall back to trainset name lookup
        return jsonify(_units_by_name(pg, value, username))


# ── helpers ──────────────────────────────────────────────────────────────────

def _name_taken(pg, name, username, is_admin_set, exclude_id=None):
    """Would saving `name` collide with an existing set of the same scope?

    Trainsets are referenced by name (trips.material_type_advanced), so names
    must be unique within a scope: personal sets are unique per owner; public
    (admin) sets are unique globally. A personal set is allowed to share a name
    with a public one — display resolves the personal one first (see
    `_units_by_name`).
    """
    if is_admin_set:
        sql = "SELECT 1 FROM trainsets WHERE is_admin AND name = :name"
        params = {"name": name}
    else:
        sql = "SELECT 1 FROM trainsets WHERE NOT is_admin AND username = :username AND name = :name"
        params = {"name": name, "username": username}
    if exclude_id is not None:
        sql += " AND id <> :id"
        params["id"] = exclude_id
    return pg.execute(sql + " LIMIT 1", params).fetchone() is not None


def _dup_name_error(name, is_admin_set):
    l = lang.get(session.get("userinfo", {}).get("lang", "en"), lang["en"])
    key = "tsDupNamePublic" if is_admin_set else "tsDupNamePersonal"
    return l.get(key, lang["en"][key]).format(name=name)



def public_trainset_info(pg, value, owner_username):
    """Resolve a trip's material_type_advanced for public display, using the
    trip owner's trainset visibility (public pages may be viewed logged out).

    Returns {'label': str|None, 'units': [slim units]} or None if nothing to show.
    Units are slimmed to display/attribution fields.
    """
    if not value:
        return None
    label = None
    units = []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, list):
        units = _enrich_units(pg, _slim_units(parsed))
    elif isinstance(parsed, dict) and isinstance(parsed.get('trainsets'), list):
        names = parsed['trainsets']
        label = ' + '.join(names)
        for name in names:
            units.extend(_units_by_name(pg, name, owner_username))
    else:
        label = value
        units = _units_by_name(pg, value, owner_username)
    if not units:
        return None
    display_fields = ('name', 'label', 'image', 'image_type', 'image_ext', 'px_per_meter',
                      '_side', '_phType', 'author', 'license', 'source')
    slim = [{k: u[k] for k in display_fields if u.get(k) is not None} for u in units]
    return {'label': label, 'units': slim}


def _units_by_name(pg, name, username):
    """Enriched units of a trainset visible to `username`, or [] if not found."""
    result = pg.execute(
        """
        SELECT units_json FROM trainsets
        WHERE name = :name AND (is_admin OR username = :username)
        ORDER BY (username = :username AND NOT is_admin) DESC
        LIMIT 1
        """,
        {"name": name, "username": username},
    )
    row = result.fetchone()
    if not row:
        return []
    return _enrich_units(pg, json.loads(row['units_json'] or '[]'))


def _rename_in_composites(pg, old_name, new_name, user_id=None):
    """Rewrite composite {"trainsets": [...]} references in trips when a set is renamed."""
    where = "material_type_advanced LIKE '{\"trainsets\"%'"
    params = {}
    if user_id is not None:
        where += " AND user_id = :user"
        params["user"] = user_id
    rows = pg.execute(
        f"SELECT trip_id, material_type_advanced FROM trips WHERE {where}", params
    ).fetchall()
    for row in rows:
        try:
            parsed = json.loads(row['material_type_advanced'])
        except (json.JSONDecodeError, TypeError):
            continue
        names = parsed.get('trainsets') if isinstance(parsed, dict) else None
        if not isinstance(names, list) or old_name not in names:
            continue
        parsed['trainsets'] = [new_name if n == old_name else n for n in names]
        pg.execute(
            "UPDATE trips SET material_type_advanced = :val WHERE trip_id = :tid",
            {"val": json.dumps(parsed), "tid": row['trip_id']},
        )


def _slim_units(units):
    """Keep only wagon name, flip-side, and placeholder type — all other data lives in the wagons table."""
    slim = []
    for u in units:
        if 'name' not in u:
            continue
        s = {'name': u['name'], '_side': u.get('_side', 'L')}
        if u.get('_phType'):
            s['_phType'] = u['_phType']
        slim.append(s)
    return slim


def _enrich_units(pg, slim_units):
    """Join slim unit refs with the wagons table to restore display fields."""
    enriched = []
    for u in slim_units:
        result = pg.execute(
            "SELECT category, subcategory, label, era, image, name, notes, image_type, image_ext, px_per_meter, author, license, source FROM wagons WHERE name = :name",
            {"name": u['name']},
        )
        wagon = result.fetchone()
        if wagon:
            unit = dict(wagon)
        else:
            unit = {
                'name': u['name'], 'label': u.get('label', u['name']),
                'image': None, 'image_type': 'plain', 'image_ext': 'gif', 'px_per_meter': None,
                'category': None, 'subcategory': None, 'era': None,
                'notes': None, 'source': None, 'author': None, 'license': None,
            }
        unit['_side'] = u.get('_side', 'L')
        if u.get('_phType'):
            unit['_phType'] = u['_phType']
        enriched.append(unit)
    return enriched
