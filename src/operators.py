import os
from datetime import datetime
from typing import Literal, NotRequired, TypedDict

from src.pg import pg_session

LOGO_UPLOAD_FOLDER = "static/images/operator_logos/new"


class OperatorsRepository:
    @classmethod
    def get_operators(cls):
        with pg_session() as pg:
            result = pg.execute("""
                SELECT o.operator_id, o.short_name, o.long_name, o.operator_type, ol.logo_url
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
        with pg_session() as pg:
            result = pg.execute(
                """
                INSERT INTO operators (short_name, long_name, operator_type)
                VALUES (:short_name, :long_name, :operator_type)
                RETURNING operator_id, short_name, long_name, operator_type
            """,
                {
                    "short_name": short_name,
                    "long_name": long_name,
                    "operator_type": operator_type,
                },
            )
            columns = ["operator_id", "short_name", "long_name", "operator_type"]
            return dict(zip(columns, result.fetchone()))

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
        if field == "operator_type" and value not in (
            "operator",
            "accommodation",
            "car",
            "poi",
        ):
            return {"success": False, "error": "invalid operator_type"}

        with pg_session() as pg:
            pg.execute(
                f"UPDATE operators SET {field} = :value WHERE operator_id = :operator_id",
                {"value": value, "operator_id": operator_id},
            )
            return {"success": True}

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
