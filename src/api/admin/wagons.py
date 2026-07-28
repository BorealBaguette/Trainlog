import json
import logging
import re
import shlex
import time
from collections import Counter
from pathlib import Path

from flask import Blueprint, jsonify, request

from src.pg import pg_session
from src.utils import admin_required

logger = logging.getLogger(__name__)

wagons_admin_blueprint = Blueprint("admin_wagons", __name__)

_EDITABLE_FIELDS = {"label", "category", "subcategory", "era", "source", "notes",
                    "image_type", "image", "author", "license",
                    "gauge", "updated_on", "image_ext", "px_per_meter", "countries"}
_VALID_IMAGE_TYPES = {"plain", "sides", "sides_L", "sides_R"}
_VALID_IMAGE_EXTS  = {"gif", "png"}
_COL_MAP = {0: "name", 1: "label", 2: "category", 3: "subcategory",
            4: "era", 5: "countries", 6: "uses", 7: "image_type", 8: "image"}

WAGONS_ROOT   = Path("static/images/wagons").resolve()
CUSTOM_FOLDER = "images/custom"          # relative to WAGONS_ROOT, stored in DB


_USAGE_TTL = 300          # seconds; the admin table re-queries on every page change
_usage_cache: dict = {"at": 0.0, "counts": {}}


def _wagon_usage() -> dict[str, int]:
    """
    Trips using each wagon, keyed by wagon name.

    There is no foreign key to follow: trips.material_type_advanced holds either an
    inline JSON array of units, a composite {"trainsets": [names]}, or a bare trainset
    name. Rather than cast that column to jsonb in SQL — one malformed row would abort
    the whole query — group the distinct values, then parse them in Python where a bad
    value can be skipped. Distinct compositions are far fewer than trips, so this stays
    cheap, and the result is cached for a few minutes.
    """
    now = time.time()
    if now - _usage_cache["at"] < _USAGE_TTL:
        return _usage_cache["counts"]

    counts: Counter = Counter()
    try:
        with pg_session() as pg:
            sets = {}
            for r in pg.execute("SELECT name, units_json FROM trainsets").fetchall():
                try:
                    units = json.loads(r["units_json"] or "[]")
                except (TypeError, ValueError):
                    continue
                names = [u.get("name") for u in units if isinstance(u, dict) and u.get("name")]
                # Names collide between personal and public sets; union them rather
                # than guessing which one a given trip meant.
                sets.setdefault(r["name"], set()).update(names)

            rows = pg.execute(
                """
                SELECT material_type_advanced AS mta, COUNT(*) AS n
                FROM trips
                WHERE material_type_advanced IS NOT NULL AND material_type_advanced <> ''
                GROUP BY material_type_advanced
                """
            ).fetchall()

        for row in rows:
            value, n = (row["mta"] or "").strip(), row["n"]
            names: set[str] = set()
            if value.startswith("["):
                try:
                    names = {u.get("name") for u in json.loads(value)
                             if isinstance(u, dict) and u.get("name")}
                except (TypeError, ValueError):
                    continue
            elif value.startswith("{"):
                try:
                    for set_name in json.loads(value).get("trainsets") or []:
                        names |= sets.get(set_name, set())
                except (TypeError, ValueError, AttributeError):
                    continue
            else:
                names = sets.get(value, set())

            for wagon in names:
                counts[wagon] += n
    except Exception as e:                        # never break the listing over a stat
        logger.warning("wagon usage count failed: %s", e)
        return _usage_cache["counts"]

    _usage_cache.update(at=now, counts=dict(counts))
    return _usage_cache["counts"]


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


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _detect_ext(f) -> str:
    """Sniff an uploaded image's format from its magic bytes; return 'gif' or 'png'."""
    header = f.read(8)
    f.seek(0)
    if header[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if header[:8] == _PNG_MAGIC:
        return "png"
    raise ValueError("not a valid GIF or PNG file")


def _save_image(f, rel_path_noext: str) -> str:
    """Validate and save an uploaded GIF/PNG under WAGONS_ROOT, appending the detected
    extension to rel_path_noext. Returns the extension ('gif' | 'png')."""
    ext = _detect_ext(f)
    rel_path = f"{rel_path_noext}.{ext}"
    target = (WAGONS_ROOT / rel_path).resolve()
    target.relative_to(WAGONS_ROOT)          # path-traversal guard
    target.parent.mkdir(parents=True, exist_ok=True)
    f.save(str(target))
    return ext


@wagons_admin_blueprint.route("", methods=["GET"])
@admin_required
def list_wagons():
    draw   = request.args.get("draw",   1,   type=int)
    start  = request.args.get("start",  0,   type=int)
    length = request.args.get("length", 25,  type=int)
    search = request.args.get("search[value]", "").strip()

    order_col = _COL_MAP.get(request.args.get("order[0][column]", 0, type=int), "label")
    order_dir = "ASC" if request.args.get("order[0][dir]", "asc") == "asc" else "DESC"

    # Must run before the session below opens — pg_session() refuses to nest.
    usage = _wagon_usage()

    with pg_session() as pg:
        total = pg.execute("SELECT COUNT(*) FROM wagons").scalar()

        if search:
            try:
                terms = [t.strip() for t in shlex.split(search) if t.strip()]
            except ValueError:
                terms = search.split()
            # Text fields searched with ILIKE; gauge cast to text for numeric match
            _text_fields = ["label", "category", "subcategory", "notes",
                            "name", "image", "era", "source", "author", "license",
                            "countries"]
            _field_exprs = [f"{f} ILIKE :t{{i}}" for f in _text_fields] + \
                           ["gauge::text ILIKE :t{i}"]
            qparams = {"limit": length, "offset": start}
            term_clauses = []
            for i, term in enumerate(terms):
                qparams[f"t{i}"] = f"%{term}%"
                exprs = [e.format(i=i) for e in _field_exprs]
                term_clauses.append("(" + " OR ".join(exprs) + ")")
            where    = "WHERE " + " AND ".join(term_clauses)
            filtered = pg.execute(
                f"SELECT COUNT(*) FROM wagons {where}", qparams
            ).scalar()
        else:
            where    = ""
            filtered = total
            qparams  = {"limit": length, "offset": start}

        # Usage counts live in Python (see _wagon_usage), so they are fed back in as a
        # pair of arrays and joined — that keeps sorting by popularity in SQL, which
        # server-side paging requires.
        qparams["u_names"]  = list(usage.keys())
        qparams["u_counts"] = list(usage.values())

        data = [dict(r) for r in pg.execute(
            f"""
            SELECT name, label, category, subcategory, era, image, notes,
                   source, image_type, image_ext, px_per_meter,
                   author, license, gauge, countries,
                   COALESCE(u.ucount, 0) AS uses
            FROM wagons
            LEFT JOIN unnest(CAST(:u_names AS text[]), CAST(:u_counts AS bigint[]))
                   AS u(uname, ucount) ON u.uname = wagons.name
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

    if "image_ext" in updates and updates["image_ext"] not in _VALID_IMAGE_EXTS:
        return jsonify({"error": "invalid image_ext"}), 400

    if "gauge" in updates:
        g = updates["gauge"]
        if g == "" or g is None:
            updates["gauge"] = None
        else:
            try:
                updates["gauge"] = int(g)
            except (ValueError, TypeError):
                return jsonify({"error": "gauge must be an integer"}), 400

    if "px_per_meter" in updates:
        p = updates["px_per_meter"]
        if p == "" or p is None:
            updates["px_per_meter"] = None
        else:
            try:
                updates["px_per_meter"] = float(p)
            except (ValueError, TypeError):
                return jsonify({"error": "px_per_meter must be a number"}), 400
            if updates["px_per_meter"] <= 0:
                return jsonify({"error": "px_per_meter must be positive"}), 400

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

    if field == "image_ext" and value not in _VALID_IMAGE_EXTS:
        return jsonify({"error": "invalid image_ext"}), 400

    new_value = value or None
    if field == "px_per_meter" and new_value is not None:
        try:
            new_value = float(new_value)
        except (ValueError, TypeError):
            return jsonify({"error": "px_per_meter must be a number"}), 400
        if new_value <= 0:
            return jsonify({"error": "px_per_meter must be positive"}), 400

    with pg_session() as pg:
        if not pg.execute("SELECT 1 FROM wagons WHERE name = :n", {"n": wname}).fetchone():
            return jsonify({"error": "not found"}), 404
        pg.execute(
            f"UPDATE wagons SET {field} = :value WHERE name = :n",
            {"value": new_value, "n": wname},
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

    rel_noext = f"{image_path}_L" if side == "L" else \
                f"{image_path}_R" if side == "R" else \
                image_path

    try:
        ext = _save_image(f, rel_noext)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    with pg_session() as pg:
        pg.execute(
            "UPDATE wagons SET image_ext = :e WHERE name = :n",
            {"e": ext, "n": wname},
        )

    return jsonify({"saved": f"{rel_noext}.{ext}", "image_ext": ext}), 200


@wagons_admin_blueprint.route("", methods=["POST"])
@admin_required
def create_wagon():
    """Create a wagon from multipart form data (image upload included)."""
    label      = (request.form.get("label")      or "").strip()
    name_in    = (request.form.get("name")       or "").strip()
    category   = (request.form.get("category")   or "").strip()
    subcategory= (request.form.get("subcategory") or "").strip()
    era        = (request.form.get("era")         or "").strip()
    notes      = (request.form.get("notes")       or "").strip()
    author     = (request.form.get("author")      or "").strip()
    license_   = (request.form.get("license")     or "").strip()
    gauge_str  = (request.form.get("gauge")       or "").strip()
    # The selector posts one value per country; store them the way tickets do
    countries  = ",".join(
        c for c in dict.fromkeys(
            v.strip().upper() for v in request.form.getlist("countries[]") if v.strip()
        )
    ) or None
    ppm_str    = (request.form.get("px_per_meter") or "").strip()
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

    px_per_meter = None
    if ppm_str:
        try:
            px_per_meter = float(ppm_str)
        except ValueError:
            return jsonify({"error": "px_per_meter must be a number"}), 400
        if px_per_meter <= 0:
            return jsonify({"error": "px_per_meter must be positive"}), 400

    # PK / image base path: explicit if given, otherwise derived from the label.
    # An explicit name must be free — silently suffixing it would surprise the caller.
    with pg_session() as pg:
        if name_in:
            name = _sanitize_name(name_in)
            if pg.execute(
                "SELECT 1 FROM wagons WHERE name = :n", {"n": name}
            ).fetchone():
                return jsonify({"error": f'name "{name}" already exists'}), 409
        else:
            name = _unique_name(pg, _sanitize_name(label))

    image_path = f"{CUSTOM_FOLDER}/{name}"

    # Save uploaded files. All sides of one wagon share a single format; the last
    # detected extension wins for the row (defaulting to 'gif' if nothing was uploaded).
    errors = []
    image_ext = "gif"
    if image_type == "sides":
        for side, key in (("L", "file_l"), ("R", "file_r")):
            f = request.files.get(key)
            if f and f.filename:
                try:
                    image_ext = _save_image(f, f"{image_path}_{side}")
                except ValueError as e:
                    errors.append(f"{key}: {e}")
    elif image_type == "sides_L":
        f = request.files.get("file")
        if f and f.filename:
            try:
                image_ext = _save_image(f, f"{image_path}_L")
            except ValueError as e:
                errors.append(f"file: {e}")
    elif image_type == "sides_R":
        f = request.files.get("file")
        if f and f.filename:
            try:
                image_ext = _save_image(f, f"{image_path}_R")
            except ValueError as e:
                errors.append(f"file: {e}")
    else:
        f = request.files.get("file")
        if f and f.filename:
            try:
                image_ext = _save_image(f, image_path)
            except ValueError as e:
                errors.append(f"file: {e}")

    if errors:
        return jsonify({"error": "; ".join(errors)}), 400

    with pg_session() as pg:
        pg.execute(
            """
            INSERT INTO wagons (category, subcategory, label, era, image, name,
                                notes, image_type, image_ext, px_per_meter, author, license,
                                gauge, countries)
            VALUES (:category, :subcategory, :label, :era, :image, :name,
                    :notes, :image_type, :image_ext, :px_per_meter, :author, :license,
                    :gauge, :countries)
            """,
            {
                "category":     category or None,
                "subcategory":  subcategory or None,
                "label":        label,
                "era":          era or None,
                "image":        image_path,
                "name":         name,
                "notes":        notes or None,
                "image_type":   image_type,
                "image_ext":    image_ext,
                "px_per_meter": px_per_meter,
                "author":       author or None,
                "license":      license_ or None,
                "gauge":        gauge,
                "countries":    countries,
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
