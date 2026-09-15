from flask import Blueprint, jsonify

from src.pg import pg_session
from src.sql.plans import get_user_plans_query
from src.utils import get_user_id, login_required

plans_api_blueprint = Blueprint("plans_api", __name__)


@plans_api_blueprint.route("/u/<username>/plans/list")
@login_required
def list_user_plans(username):
    """Plans of the logged-in user, for the trip list's "move to plan" picker."""
    with pg_session() as pg:
        rows = pg.execute(
            get_user_plans_query(), {"user_id": get_user_id(username)}
        ).fetchall()
    return jsonify(
        [
            {
                "uuid": r._mapping["uuid"],
                "name": r._mapping["name"],
                "trip_count": r._mapping["trip_count"],
                "archived": r._mapping["archived"],
            }
            for r in rows
        ]
    )
