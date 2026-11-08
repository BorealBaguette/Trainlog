"""The link the tag list copies for a tag, showing a chosen mix of prices and CO₂."""

from flask import Blueprint, abort, jsonify, request, url_for

from src import share_view
from src.pg import pg_session
from src.trip_selections import store_tag_key
from src.users import User
from src.utils import login_required

tag_share_blueprint = Blueprint("tag_share", __name__)


@tag_share_blueprint.route("/u/<username>/tag/<int:tag_id>/share", methods=["POST"])
@login_required
def share_tag(username, tag_id):
    show = request.form.get("show")
    if not show or set(show) - {"p", "c", "n"}:
        abort(400)
    with pg_session() as pg:
        tag = pg.execute(
            "SELECT uuid FROM tags WHERE uid = :uid AND username = :username",
            {"uid": tag_id, "username": username},
        ).fetchone()
    if tag is None:
        abort(401)

    wanted = share_view.decode(show)
    owner = User.query.filter_by(username=username).first()
    if wanted == share_view.owner_defaults([owner]):
        return jsonify(url=url_for("public_trip", tagId=tag["uuid"]))
    key = store_tag_key(tag["uuid"], share_view.encode(wanted))
    return jsonify(url=url_for("public_trip", tripIds=key))
