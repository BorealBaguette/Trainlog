import logging
import os
from datetime import datetime
from typing import Literal

from flask import Blueprint, abort, jsonify, request
from sqlalchemy import bindparam, text
from werkzeug.utils import secure_filename

from py.utils import validate_png_file
from src.operators import OperatorsRepository
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

    if operator_type not in ("operator", "accommodation", "car", "poi"):
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
    with pg_session() as pg:
        # Get all distinct operators from trips with their types and counts
        result = pg.execute("""
            SELECT operator, trip_type
            FROM trips
            WHERE operator IS NOT NULL
              AND operator != ''
              AND trip_type not in ('car', 'walk', 'cycle', 'poi', 'accommodation', 'restaurant')
        """)
        trip_rows = result.fetchall()

        if not trip_rows or len(trip_rows) == 0:
            return jsonify({"missing_operators": [], "total_count": 0, "by_type": {}})

        # Split comma-separated operators and count them by type
        operator_counts = {}  # {(operator, type): count}
        for row in trip_rows:
            operators = [op.strip() for op in str(row["operator"]).split(",")]
            trip_type = row["trip_type"]
            for operator in operators:
                if operator:  # Skip empty strings
                    key = (operator, trip_type)
                    operator_counts[key] = operator_counts.get(key, 0) + 1

        if not operator_counts:
            return jsonify({"missing_operators": [], "total_count": 0, "by_type": {}})

        # Get all unique operators
        all_operators = list(set(op for op, _ in operator_counts.keys()))
        existing_operators = set()

        # Check which operators exist in batches
        batch_size = 999
        stmt = text(
            "SELECT DISTINCT short_name FROM operators WHERE short_name IN :batch"
        ).bindparams(bindparam("batch", expanding=True))
        for i in range(0, len(all_operators), batch_size):
            batch = all_operators[i : i + batch_size]
            result = pg.execute(stmt, {"batch": batch})
            existing_operators.update(row["short_name"] for row in result.fetchall())

        # Build results by type
        by_type = {}
        total_occurrences = 0

        for (operator, trip_type), count in operator_counts.items():
            if operator not in existing_operators:
                if trip_type not in by_type:
                    by_type[trip_type] = []
                by_type[trip_type].append({"operator": operator, "occurrences": count})
                total_occurrences += count

        # Sort each type's operators by occurrences descending
        for trip_type in by_type:
            by_type[trip_type].sort(key=lambda x: x["occurrences"], reverse=True)

        return jsonify(
            {
                "missing_operators_by_type": by_type,
                "total_occurrences": total_occurrences,
                "unique_missing_operators": sum(len(ops) for ops in by_type.values()),
            }
        )
