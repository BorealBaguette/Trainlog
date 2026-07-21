import csv
import logging
import os
from datetime import datetime
from io import StringIO
from typing import Literal

from flask import Blueprint, abort, jsonify, make_response, request
from werkzeug.utils import secure_filename

from py.utils import validate_png_file
from src.operators import (
    OPERATOR_TYPES,
    OperatorsRepository,
    check_trip_operators_consistency,
    rebuild_trip_operators,
    resync_operator_aliases,
)
from src.pg import pg_session
from src.utils import admin_required, getUser, parse_date

logger = logging.getLogger(__name__)

LOGO_UPLOAD_FOLDER = "static/images/operator_logos/new"
os.makedirs(LOGO_UPLOAD_FOLDER, exist_ok=True)

operators_api_blueprint = Blueprint("admin_operators", __name__)


@operators_api_blueprint.route("", methods=["GET"])
@admin_required
def get_operators():
    operators = OperatorsRepository.get_operators()
    return jsonify({"operators": operators}), 200


@operators_api_blueprint.route("", methods=["POST"])
@admin_required
def add_operator():
    short_name = request.form.get("short_name")
    long_name = request.form.get("long_name")
    operator_type = request.form.get("operator_type")
    logo = request.files.get("logo")

    if not logo:
        abort(400, description="logo is required")

    if operator_type not in OPERATOR_TYPES:
        abort(400, description="invalid operator_type")

    if len(short_name) == 0:
        abort(400, description="short_name is required")

    if len(long_name) == 0:
        abort(400, description="long_name is required")

    try:
        validate_png_file(logo)

        operator = OperatorsRepository.add(short_name, long_name, operator_type)
        operator_id = operator["operator_id"]

        filename = secure_filename(
            f"{operator_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        )
        logo.save(os.path.join(LOGO_UPLOAD_FOLDER, filename))

        OperatorsRepository.add_operator_logo(
            operator_id, f"images/operator_logos/new/{filename}", None
        )

        # Log the successful addition to the save log
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open("logs/operator_logo_log.txt", "a", encoding="utf-8") as log:
            log.write(
                f"{current_time} - From: {getUser()} - Operator added: {short_name} (ID: {operator_id})\n"
            )

        return jsonify({"operator": operator}), 201
    except Exception as e:
        # Log the error
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open("logs/operator_logo_save_errors.txt", "a", encoding="utf-8") as log:
            log.write(
                f"{current_time} - From: {getUser()} - Error: {e} - Operator: {short_name}\n"
            )
        return jsonify({"status": "error", "message": str(e)}), 500


@operators_api_blueprint.route(
    "<int:operator_id>/<any(short_name, long_name, operator_type):field>",
    methods=["PUT"],
)
@admin_required
def update_operator_field(
    operator_id: int, field: Literal["short_name", "long_name", "operator_type"]
):
    new_value = request.get_data(as_text=True)
    if not OperatorsRepository.operator_exists(operator_id):
        abort(404, description="operator not found")

    result = OperatorsRepository.update_operator_field(operator_id, field, new_value)
    if not result["success"]:
        abort(400, description=result["error"])
    return "", 204


@operators_api_blueprint.route("<int:operator_id>", methods=["DELETE"])
@admin_required
def delete_operator(operator_id: int):
    OperatorsRepository.delete(operator_id)
    return "", 204


@operators_api_blueprint.route("<int:operator_id>/logos", methods=["GET"])
@admin_required
def get_operator_logos(operator_id: int):
    operator_logos = OperatorsRepository.get_operator_logos(operator_id)
    return jsonify({"logos": operator_logos})


@operators_api_blueprint.route("<int:operator_id>/logos", methods=["POST"])
@admin_required
def add_operator_logo(operator_id: int):
    if not OperatorsRepository.operator_exists(operator_id):
        abort(404, description="operator not found")

    logo = request.files.get("logo")
    effective_date = request.form.get("effective_date", type=parse_date)

    logger.info(len(request.form.keys()))
    if not logo:
        abort(400, description="logo is required")

    try:
        validate_png_file(logo)

        filename = secure_filename(
            f"{operator_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        )
        logo.save(os.path.join(LOGO_UPLOAD_FOLDER, filename))

        operator_logo = OperatorsRepository.add_operator_logo(
            operator_id, f"images/operator_logos/new/{filename}", effective_date
        )

        # Log the successful addition to the save log
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open("logs/operator_logo_log.txt", "a", encoding="utf-8") as log:
            log.write(
                f"{current_time} - From: {getUser()} - Logo Added for Operator ID: {operator_logo} - Filename: {filename}\n"
            )

        return jsonify({"logo": operator_logo}), 201
    except Exception as e:
        # Log the error
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open("logs/operator_logo_save_errors.txt", "a", encoding="utf-8") as log:
            log.write(
                f"{current_time} - From: {getUser()} - Error: {e} - Operator ID: {operator_id}\n"
            )
        return jsonify({"status": "error", "message": str(e)}), 500


@operators_api_blueprint.route("logos/<int:logo_id>", methods=["DELETE"])
@admin_required
def delete_operator_logo(logo_id: int):
    OperatorsRepository.delete_operator_logo(logo_id)
    return "", 204


@operators_api_blueprint.route("missing-operators", methods=["GET"])
@admin_required
def get_missing_operators():
    """Spellings used on trips that resolve to no operator, by occurrence.

    This is the alias-assignment work queue. It used to read every trip, split the
    operator text in Python and test the pieces against operators.short_name in
    batches of 999; resolution now lives in trip_operators, so an unresolved name is
    simply operator_id IS NULL and the whole thing is one grouped query.

    Response shape is unchanged so the existing UI keeps working.
    """
    with pg_session() as pg:
        rows = pg.execute("""
            SELECT tv.raw_name AS operator, t.trip_type, COUNT(*) AS occurrences
            FROM trip_operators tv
            JOIN trips t ON t.trip_id = tv.trip_id
            WHERE tv.operator_id IS NULL
              AND t.trip_type NOT IN
                  ('car', 'walk', 'cycle', 'poi', 'accommodation', 'restaurant')
            GROUP BY tv.raw_name, t.trip_type
            ORDER BY occurrences DESC
        """).fetchall()

    by_type = {}
    total_occurrences = 0
    for row in rows:
        by_type.setdefault(row["trip_type"], []).append(
            {"operator": row["operator"], "occurrences": row["occurrences"]}
        )
        total_occurrences += row["occurrences"]

    return jsonify(
        {
            "missing_operators_by_type": by_type,
            "total_occurrences": total_occurrences,
            "unique_missing_operators": sum(len(ops) for ops in by_type.values()),
        }
    )


@operators_api_blueprint.route("suggest", methods=["GET"])
@admin_required
def suggest_operators():
    """Operators whose names look like `name`, to pre-fill the alias picker.

    Uses pg_trgm similarity so a near-miss spelling ("Deutsche Bhan") still finds its
    operator; the admin confirms rather than searching by hand.
    """
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"suggestions": []})

    with pg_session() as pg:
        # similarity() rather than the `%` operator: a literal percent sign in a
        # raw statement gets mangled by the driver's own parameter escaping, and an
        # explicit threshold is clearer than depending on pg_trgm.similarity_threshold.
        rows = pg.execute(
            """
            SELECT o.operator_id, o.short_name, o.long_name, o.operator_type,
                   MAX(similarity(a.alias, :name)) AS score
            FROM operator_aliases a
            JOIN operators o ON o.operator_id = a.operator_id
            WHERE similarity(a.alias, :name) > 0.25
            GROUP BY o.operator_id, o.short_name, o.long_name, o.operator_type
            ORDER BY score DESC
            LIMIT 8
            """,
            {"name": name},
        ).fetchall()

    return jsonify(
        {
            "suggestions": [
                {
                    "operator_id": r["operator_id"],
                    "short_name": r["short_name"],
                    "long_name": r["long_name"],
                    "operator_type": r["operator_type"],
                    "score": float(r["score"]),
                }
                for r in rows
            ]
        }
    )


@operators_api_blueprint.route("<int:operator_id>/aliases", methods=["GET"])
@admin_required
def get_operator_aliases(operator_id: int):
    if not OperatorsRepository.operator_exists(operator_id):
        abort(404, description="operator not found")
    return jsonify({"aliases": OperatorsRepository.get_aliases(operator_id)})


@operators_api_blueprint.route("<int:operator_id>/aliases", methods=["POST"])
@admin_required
def add_operator_alias(operator_id: int):
    if not OperatorsRepository.operator_exists(operator_id):
        abort(404, description="operator not found")

    payload = request.get_json(silent=True) or request.form
    alias = (payload.get("alias") or "").strip()
    lang = (payload.get("lang") or "").strip() or None

    if not alias:
        abort(400, description="alias is required")

    # One spelling means one operator per pool. Report who holds it rather than
    # letting the unique index surface as a 500 — the admin's next step is a merge.
    conflict = OperatorsRepository.find_alias_conflict(operator_id, alias)
    if conflict is not None:
        if conflict["operator_id"] == operator_id:
            return jsonify(
                {"status": "error", "message": "this operator already has that alias"}
            ), 409
        return jsonify(
            {
                "status": "conflict",
                "message": f"'{alias}' already resolves to {conflict['short_name']}",
                "operator": conflict,
            }
        ), 409

    try:
        return jsonify({"alias": OperatorsRepository.add_alias(operator_id, alias, lang)}), 201
    except Exception as e:
        logger.exception("failed to add alias %r to operator %s", alias, operator_id)
        return jsonify({"status": "error", "message": str(e)}), 500


@operators_api_blueprint.route("aliases/<int:alias_id>", methods=["DELETE"])
@admin_required
def delete_operator_alias(alias_id: int):
    result = OperatorsRepository.delete_alias(alias_id)
    if result is None:
        abort(404, description="alias not found")
    return jsonify(result), 200


@operators_api_blueprint.route("<int:operator_id>/group", methods=["GET"])
@admin_required
def get_operator_group(operator_id: int):
    if not OperatorsRepository.operator_exists(operator_id):
        abort(404, description="operator not found")
    return jsonify({"members": OperatorsRepository.get_group_members(operator_id)})


@operators_api_blueprint.route("<int:operator_id>/group", methods=["POST"])
@admin_required
def link_operator_group(operator_id: int):
    """Count another operator as the same company, without either absorbing the other.

    For the SBB/CFF/FFS case: both keep their name, logo and trips, and only the
    aggregate views add them together. Contrast with merge, which is for genuine
    duplicate rows and does delete one.
    """
    payload = request.get_json(silent=True) or request.form
    try:
        other_id = int(payload["operator_id"])
    except (KeyError, TypeError, ValueError):
        abort(400, description="operator_id is required")

    if other_id == operator_id:
        abort(400, description="cannot group an operator with itself")
    for oid in (operator_id, other_id):
        if not OperatorsRepository.operator_exists(oid):
            abort(404, description=f"operator {oid} not found")

    result = OperatorsRepository.link_operators(operator_id, other_id)
    if not result["success"]:
        abort(400, description=result["error"])
    return jsonify(result), 200


@operators_api_blueprint.route("<int:operator_id>/group", methods=["DELETE"])
@admin_required
def unlink_operator_group(operator_id: int):
    if not OperatorsRepository.operator_exists(operator_id):
        abort(404, description="operator not found")
    return jsonify(OperatorsRepository.unlink_operator(operator_id)), 200


@operators_api_blueprint.route("<int:source_id>/merge", methods=["POST"])
@admin_required
def merge_operators(source_id: int):
    # A JSON body is a plain dict and a form body a MultiDict; indexing works on
    # both, unlike MultiDict's type= coercion.
    payload = request.get_json(silent=True) or request.form
    try:
        target_id = int(payload["target_id"])
    except (KeyError, TypeError, ValueError):
        abort(400, description="target_id is required")

    if source_id == target_id:
        abort(400, description="cannot merge an operator into itself")
    for oid in (source_id, target_id):
        if not OperatorsRepository.operator_exists(oid):
            abort(404, description=f"operator {oid} not found")

    result = OperatorsRepository.merge(source_id, target_id)
    if not result["success"]:
        abort(400, description=result["error"])
    return jsonify(result), 200


@operators_api_blueprint.route("export.csv", methods=["GET"])
@admin_required
def export_operators_csv():
    """Operators and their aliases as reviewable CSV.

    `slug` is the natural key, so the file round-trips without exposing numeric ids —
    the same principle as the trip export.
    """
    with pg_session() as pg:
        rows = pg.execute("""
            SELECT o.slug, o.short_name, o.long_name, o.operator_type,
                   COALESCE(string_agg(a.alias, '|' ORDER BY a.alias)
                            FILTER (WHERE a.kind = 'alias'), '') AS aliases
            FROM operators o
            LEFT JOIN operator_aliases a ON a.operator_id = o.operator_id
            GROUP BY o.operator_id, o.slug, o.short_name, o.long_name, o.operator_type
            ORDER BY o.slug
        """).fetchall()

    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["slug", "short_name", "long_name", "operator_type", "aliases"])
    for r in rows:
        writer.writerow(
            [r["slug"], r["short_name"], r["long_name"], r["operator_type"], r["aliases"]]
        )

    response = make_response(buffer.getvalue())
    response.headers["Content-Disposition"] = (
        f"attachment; filename=trainlog_operators_{datetime.now():%Y-%m-%d_%H%M%S}.csv"
    )
    response.headers["Content-type"] = "text/csv"
    return response


@operators_api_blueprint.route("import.csv", methods=["POST"])
@admin_required
def import_operators_csv():
    """Apply an edited operators CSV: rename operators and add aliases by slug.

    Deliberately additive — it never deletes an operator or an alias, so a truncated
    or partial file cannot destroy curation. Rows whose slug is unknown are reported
    rather than silently creating operators (a typo would otherwise make one).
    Aliases already owned by another operator are skipped and reported: taking them
    over is a merge, which is an explicit action.
    """
    upload = request.files.get("file")
    if not upload:
        abort(400, description="file is required")

    try:
        reader = csv.DictReader(StringIO(upload.read().decode("utf-8-sig")))
        rows = list(reader)
    except Exception as e:
        abort(400, description=f"could not read CSV: {e}")

    required = {"slug", "short_name", "long_name", "operator_type"}
    if not rows or not required.issubset(set(rows[0].keys())):
        abort(400, description=f"CSV must have columns: {', '.join(sorted(required))}")

    renamed, aliases_added, unknown_slugs, skipped_aliases = 0, 0, [], []
    # (operator_id, alias) rather than just the operator: re-resolution has to know
    # the new spelling to pick up the still-unresolved trips using it.
    added_aliases = []

    with pg_session() as pg:
        for row in rows:
            slug = (row.get("slug") or "").strip()
            if not slug:
                continue
            operator = pg.execute(
                "SELECT operator_id, short_name, long_name, operator_type"
                " FROM operators WHERE slug = :slug",
                {"slug": slug},
            ).fetchone()
            if operator is None:
                unknown_slugs.append(slug)
                continue

            operator_id = operator["operator_id"]
            short_name = (row.get("short_name") or "").strip()
            long_name = (row.get("long_name") or "").strip()
            operator_type = (row.get("operator_type") or "").strip()

            if operator_type and operator_type not in OPERATOR_TYPES:
                abort(400, description=f"invalid operator_type {operator_type!r}")

            if short_name and long_name and (
                short_name != operator["short_name"]
                or long_name != operator["long_name"]
                or operator_type != operator["operator_type"]
            ):
                pg.execute(
                    "UPDATE operators SET short_name = :s, long_name = :l,"
                    " operator_type = :t WHERE operator_id = :id",
                    {
                        "s": short_name,
                        "l": long_name,
                        "t": operator_type or operator["operator_type"],
                        "id": operator_id,
                    },
                )
                renamed += 1

            for alias in (row.get("aliases") or "").split("|"):
                alias = alias.strip()
                if not alias:
                    continue
                owner = pg.execute(
                    """
                    SELECT a.operator_id FROM operator_aliases a
                    WHERE a.normalized = operator_normalize(:alias)
                      AND a.operator_type = (
                          SELECT operator_type FROM operators WHERE operator_id = :id)
                    """,
                    {"alias": alias, "id": operator_id},
                ).fetchone()
                if owner is not None:
                    if owner["operator_id"] != operator_id:
                        skipped_aliases.append({"alias": alias, "slug": slug})
                    continue
                pg.execute(
                    "INSERT INTO operator_aliases (operator_id, operator_type, alias, kind)"
                    " SELECT :id, o.operator_type, :alias, 'alias' FROM operators o"
                    " WHERE o.operator_id = :id",
                    {"id": operator_id, "alias": alias},
                )
                aliases_added += 1
                added_aliases.append((operator_id, alias))

        for operator_id, alias in added_aliases:
            resync_operator_aliases(operator_id, alias=alias, pg_session_=pg)

    logger.info(
        "operators CSV imported by %s: %s renamed, %s aliases added",
        getUser(),
        renamed,
        aliases_added,
    )
    return jsonify(
        {
            "renamed": renamed,
            "aliases_added": aliases_added,
            "unknown_slugs": unknown_slugs,
            "skipped_aliases": skipped_aliases,
        }
    ), 200


@operators_api_blueprint.route("trip-operators/check", methods=["GET"])
@admin_required
def check_trip_operators():
    """Is the derived trip_operators table still in step with trips.operator?"""
    return jsonify(check_trip_operators_consistency())


@operators_api_blueprint.route("trip-operators/rebuild", methods=["POST"])
@admin_required
def rebuild_trip_operators_endpoint():
    """Rebuild the derived table from scratch — the cure for any drift."""
    rows = rebuild_trip_operators()
    logger.info("trip_operators rebuilt by %s: %s rows", getUser(), rows)
    return jsonify({"rows": rows, **check_trip_operators_consistency()}), 200
