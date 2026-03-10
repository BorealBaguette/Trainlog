import re
from pathlib import Path

from flask import Blueprint, jsonify, request

from src.pg import pg_session
from src.utils import admin_required

wagons_admin_blueprint = Blueprint("admin_wagons", __name__)

_EDITABLE_FIELDS = {"label", "category", "subcategory", "era", "source", "notes",
                    "image_type", "line_type", "image", "author", "license",
                    "gauge", "updated_on"}
_VALID_IMAGE_TYPES = {"plain", "sides", "sides_L", "sides_R"}
_COL_MAP = {0: "name", 1: "label", 2: "category", 3: "subcategory",
            4: "era", 5: "image_type", 6: "image"}

WAGONS_ROOT   = Path("static/images/wagons").resolve()
CUSTOM_FOLDER = "images/custom"          # relative to WAGONS_ROOT, stored in DB


def _sanitize_name(label: str) -> str:
    """Convert a display label into a safe filename/PK component."""
    return re.sub(r"[^\w.-]+", "_", label).strip("_") or "wagon"


def _unique_name(pg, base: str) -> str:
    """Return base or base_2, base_3 … to avoid PK collision."""
    rows = pg.execute(
        "SELECT name FROM wagons WHERE name = :b OR name LIKE :p",
        {"b": base, "p": f"{base}_%"},
    ).fetchall()
    existing = {r["name"] for r in rows}
    if base not in existing:
        return base
    counter = 2
    while f"{base}_{counter}" in existing:
        counter += 1
    return f"{base}_{counter}"


def _save_gif(f, rel_path: str) -> None:
    """Validate and save an uploaded GIF to WAGONS_ROOT/rel_path."""
    header = f.read(6)
    f.seek(0)
    if header[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("not a valid GIF file")
    target = (WAGONS_ROOT / rel_path).resolve()
    target.relative_to(WAGONS_ROOT)          # path-traversal guard
    target.parent.mkdir(parents=True, exist_ok=True)
    f.save(str(target))


@wagons_admin_blueprint.route("", methods=["GET"])
@admin_required
def list_wagons():
    draw   = request.args.get("draw",   1,   type=int)
    start  = request.args.get("start",  0,   type=int)
    length = request.args.get("length", 25,  type=int)
    search = request.args.get("search[value]", "").strip()

    order_col = _COL_MAP.get(request.args.get("order[0][column]", 0, type=int), "label")
    order_dir = "ASC" if request.args.get("order[0][dir]", "asc") == "asc" else "DESC"

    with pg_session() as pg:
        total = pg.execute("SELECT COUNT(*) FROM wagons").scalar()

        if search:
            like     = f"%{search}%"
            where    = ("WHERE label ILIKE :like OR category ILIKE :like "
                        "OR subcategory ILIKE :like OR notes ILIKE :like "
                        "OR name ILIKE :like OR image ILIKE :like")
            filtered = pg.execute(
                f"SELECT COUNT(*) FROM wagons {where}", {"like": like}
            ).scalar()
            qparams  = {"like": like, "limit": length, "offset": start}
        else:
            where    = ""
            filtered = total
            qparams  = {"limit": length, "offset": start}

        data = [dict(r) for r in pg.execute(
            f"""
            SELECT name, label, category, subcategory, era, image, notes,
                   source, line_type, image_type, author, license, gauge
            FROM wagons
            {where}
            ORDER BY {order_col} {order_dir} NULLS LAST
            LIMIT :limit OFFSET :offset
            """,
            qparams,
        )]

    return jsonify({"draw": draw, "recordsTotal": total,
                    "recordsFiltered": filtered, "data": data})


@wagons_admin_blueprint.route("<string:wname>", methods=["PUT"])
@admin_required
def update_wagon(wname: str):
    """Bulk-update any editable fields from a JSON body."""
    data = request.get_json(silent=True) or {}
    updates = {k: v for k, v in data.items() if k in _EDITABLE_FIELDS}
    if not updates:
        return jsonify({"error": "no valid fields"}), 400

    if "image_type" in updates and updates["image_type"] not in _VALID_IMAGE_TYPES:
        return jsonify({"error": "invalid image_type"}), 400

    if "gauge" in updates:
        g = updates["gauge"]
        if g == "" or g is None:
            updates["gauge"] = None
        else:
            try:
                updates["gauge"] = int(g)
            except (ValueError, TypeError):
                return jsonify({"error": "gauge must be an integer"}), 400

    # Coerce empty strings → NULL for text fields
    for k, v in updates.items():
        if k != "gauge" and isinstance(v, str) and v.strip() == "":
            updates[k] = None

    with pg_session() as pg:
        if not pg.execute("SELECT 1 FROM wagons WHERE name = :n", {"n": wname}).fetchone():
            return jsonify({"error": "not found"}), 404
        sets = ", ".join(f"{k} = :{k}" for k in updates)
        updates["_name"] = wname
        pg.execute(f"UPDATE wagons SET {sets} WHERE name = :_name", updates)

    return "", 204


@wagons_admin_blueprint.route("<string:wname>/<field>", methods=["PUT"])
@admin_required
def update_wagon_field(wname: str, field: str):
    if field not in _EDITABLE_FIELDS:
        return jsonify({"error": "invalid field"}), 400

    value = request.get_data(as_text=True).strip()

    if field == "image_type" and value not in _VALID_IMAGE_TYPES:
        return jsonify({"error": "invalid image_type"}), 400

    with pg_session() as pg:
        if not pg.execute("SELECT 1 FROM wagons WHERE name = :n", {"n": wname}).fetchone():
            return jsonify({"error": "not found"}), 404
        pg.execute(
            f"UPDATE wagons SET {field} = :value WHERE name = :n",
            {"value": value or None, "n": wname},
        )

    return "", 204


@wagons_admin_blueprint.route("<string:wname>/image-file", methods=["POST"])
@admin_required
def upload_wagon_image(wname: str):
    """Replace an existing wagon's image file(s).
    Form fields: file (required), side ('', 'L', or 'R').
    """
    side = request.form.get("side", "").strip().upper()
    f    = request.files.get("file")
    if not f:
        return jsonify({"error": "file required"}), 400

    with pg_session() as pg:
        row = pg.execute(
            "SELECT image FROM wagons WHERE name = :n", {"n": wname}
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        image_path = row["image"]

    if not image_path:
        return jsonify({"error": "wagon has no image path — set one first"}), 400

    rel = f"{image_path}_L.gif" if side == "L" else \
          f"{image_path}_R.gif" if side == "R" else \
          f"{image_path}.gif"

    try:
        _save_gif(f, rel)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"saved": rel}), 200


@wagons_admin_blueprint.route("", methods=["POST"])
@admin_required
def create_wagon():
    """Create a wagon from multipart form data (image upload included)."""
    label      = (request.form.get("label")      or "").strip()
    category   = (request.form.get("category")   or "").strip()
    subcategory= (request.form.get("subcategory") or "").strip()
    era        = (request.form.get("era")         or "").strip()
    notes      = (request.form.get("notes")       or "").strip()
    author     = (request.form.get("author")      or "").strip()
    license_   = (request.form.get("license")     or "").strip()
    gauge_str  = (request.form.get("gauge")       or "").strip()
    image_type = (request.form.get("image_type")  or "sides").strip()

    if not label:
        return jsonify({"error": "label is required"}), 400
    if image_type not in _VALID_IMAGE_TYPES:
        return jsonify({"error": "invalid image_type"}), 400

    gauge = None
    if gauge_str:
        try:
            gauge = int(gauge_str)
        except ValueError:
            return jsonify({"error": "gauge must be an integer"}), 400

    # Auto-derive a unique PK / image base path
    with pg_session() as pg:
        name = _unique_name(pg, _sanitize_name(label))

    image_path = f"{CUSTOM_FOLDER}/{name}"

    # Save uploaded files
    errors = []
    if image_type == "sides":
        for side, key in (("L", "file_l"), ("R", "file_r")):
            f = request.files.get(key)
            if f and f.filename:
                try:
                    _save_gif(f, f"{image_path}_{side}.gif")
                except ValueError as e:
                    errors.append(f"{key}: {e}")
    elif image_type == "sides_L":
        f = request.files.get("file")
        if f and f.filename:
            try:
                _save_gif(f, f"{image_path}_L.gif")
            except ValueError as e:
                errors.append(f"file: {e}")
    elif image_type == "sides_R":
        f = request.files.get("file")
        if f and f.filename:
            try:
                _save_gif(f, f"{image_path}_R.gif")
            except ValueError as e:
                errors.append(f"file: {e}")
    else:
        f = request.files.get("file")
        if f and f.filename:
            try:
                _save_gif(f, f"{image_path}.gif")
            except ValueError as e:
                errors.append(f"file: {e}")

    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    with pg_session() as pg:
        pg.execute(
            """
            INSERT INTO wagons (category, subcategory, label, era, image, name,
                                notes, image_type, author, license, gauge)
            VALUES (:category, :subcategory, :label, :era, :image, :name,
                    :notes, :image_type, :author, :license, :gauge)
            """,
            {
                "category":    category or None,
                "subcategory": subcategory or None,
                "label":       label,
                "era":         era or None,
                "image":       image_path,
                "name":        name,
                "notes":       notes or None,
                "image_type":  image_type,
                "author":      author or None,
                "license":     license_ or None,
                "gauge":       gauge,
            },
        )

    return jsonify({"name": name, "label": label}), 201


@wagons_admin_blueprint.route("<string:wname>", methods=["DELETE"])
@admin_required
def delete_wagon(wname: str):
    with pg_session() as pg:
        if not pg.execute("SELECT 1 FROM wagons WHERE name = :n", {"n": wname}).fetchone():
            return jsonify({"error": "not found"}), 404
        pg.execute("DELETE FROM wagons WHERE name = :n", {"n": wname})

    return "", 204
