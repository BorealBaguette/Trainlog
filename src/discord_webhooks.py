"""Discord channel webhooks users add to have their trips posted to their own servers."""

import re
import time

import requests

from flask import Blueprint, jsonify, redirect, request, url_for

from src.discord_bot import post_webhook_message
from src.pg import pg_session
from src.users import User, authDb
from src.utils import login_required

discord_webhooks_blueprint = Blueprint(
    "discord_webhooks", __name__, url_prefix="/u/<username>/discord/webhooks"
)

MAX_WEBHOOKS = 5

# Only Discord's own webhook URLs are accepted: the server POSTs to whatever is
# stored here, so anything else would let a user aim it at an arbitrary host.
_WEBHOOK_URL = re.compile(
    r"^https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d+/[\w-]+"
    r"(?:\?thread_id=\d+)?$"
)


def list_webhooks(user_id):
    with pg_session() as pg:
        return pg.execute(
            "SELECT id, label, enabled, autopost FROM user_discord_webhooks "
            "WHERE user_id = :user_id ORDER BY id",
            {"user_id": user_id},
        ).fetchall()


def enabled_webhooks(user_id, auto_only=False):
    """The user's servers to post to; with auto_only, those posted to at departure."""
    with pg_session() as pg:
        return pg.execute(
            "SELECT id, url FROM user_discord_webhooks "
            "WHERE user_id = :user_id AND enabled"
            + (" AND autopost" if auto_only else ""),
            {"user_id": user_id},
        ).fetchall()


# Webhook avatars, fetched from Discord when the settings page loads. Keyed by
# webhook url -> (expires, avatar url or None); per worker, and only there so
# that reopening the page does not ask Discord again every time.
_AVATAR_CACHE = {}


def _avatar(url):
    cached = _AVATAR_CACHE.get(url)
    if cached and cached[0] > time.time():
        return cached[1]
    avatar = None
    try:
        # A GET on a webhook url needs no auth and describes the webhook.
        info = requests.get(url, timeout=5).json()
        # Only ids and hashes of the shape Discord issues end up in a URL.
        if info.get("avatar") and re.fullmatch(r"\d+", str(info.get("id"))) and re.fullmatch(
            r"\w+", str(info["avatar"])
        ):
            avatar = (
                f"https://cdn.discordapp.com/avatars/{info['id']}/{info['avatar']}.png?size=64"
            )
    except (requests.RequestException, ValueError, KeyError):
        pass
    # A failure is remembered only briefly, so a hiccup does not hide the
    # picture for an hour.
    _AVATAR_CACHE[url] = (time.time() + (3600 if avatar else 300), avatar)
    return avatar


@discord_webhooks_blueprint.route("/avatars")
@login_required
def avatars(username):
    """{webhook id: avatar url} for the user's webhooks that have a picture."""
    user = User.query.filter_by(username=username).first()
    with pg_session() as pg:
        hooks = pg.execute(
            "SELECT id, url FROM user_discord_webhooks WHERE user_id = :user_id",
            {"user_id": user.uid},
        ).fetchall()
    found = {hook["id"]: _avatar(hook["url"]) for hook in hooks}
    return jsonify({str(k): v for k, v in found.items() if v})


def _back(username, status):
    return redirect(url_for("user_settings", username=username, dw=status))


@discord_webhooks_blueprint.route("/add", methods=["POST"])
@login_required
def add(username):
    user = User.query.filter_by(username=username).first()

    url = request.form.get("url", "").strip()
    label = request.form.get("label", "").strip()[:50] or "Discord"
    if not _WEBHOOK_URL.match(url):
        return _back(username, "invalid")
    if len(list_webhooks(user.uid)) >= MAX_WEBHOOKS:
        return _back(username, "limit")

    with pg_session() as pg:
        pg.execute(
            "INSERT INTO user_discord_webhooks (user_id, label, url) "
            "VALUES (:user_id, :label, :url) ON CONFLICT (user_id, url) DO NOTHING",
            {"user_id": user.uid, "label": label, "url": url},
        )
    return _back(username, "added")


@discord_webhooks_blueprint.route("/main/toggle", methods=["POST"])
@login_required
def main_toggle(username):
    """Switch the Trainlog server on or off as a place to post to."""
    user = User.query.filter_by(username=username).first()
    user.discord_main_enabled = not user.discord_main_enabled
    authDb.session.commit()
    return _back(username, "toggled")


@discord_webhooks_blueprint.route("/main/autopost", methods=["POST"])
@login_required
def main_autopost_toggle(username):
    """Switch posting at departure on or off for the Trainlog server."""
    user = User.query.filter_by(username=username).first()
    user.discord_autopost = not user.discord_autopost
    authDb.session.commit()
    return _back(username, "toggled")


@discord_webhooks_blueprint.route("/<int:webhook_id>/autopost", methods=["POST"])
@login_required
def autopost_toggle(username, webhook_id):
    user = User.query.filter_by(username=username).first()
    with pg_session() as pg:
        pg.execute(
            "UPDATE user_discord_webhooks SET autopost = NOT autopost "
            "WHERE id = :id AND user_id = :user_id",
            {"id": webhook_id, "user_id": user.uid},
        )
    return _back(username, "toggled")


@discord_webhooks_blueprint.route("/<int:webhook_id>/delete", methods=["POST"])
@login_required
def delete(username, webhook_id):
    user = User.query.filter_by(username=username).first()
    with pg_session() as pg:
        pg.execute(
            "DELETE FROM user_discord_webhooks WHERE id = :id AND user_id = :user_id",
            {"id": webhook_id, "user_id": user.uid},
        )
    return _back(username, "deleted")


@discord_webhooks_blueprint.route("/<int:webhook_id>/toggle", methods=["POST"])
@login_required
def toggle(username, webhook_id):
    user = User.query.filter_by(username=username).first()
    with pg_session() as pg:
        pg.execute(
            "UPDATE user_discord_webhooks SET enabled = NOT enabled "
            "WHERE id = :id AND user_id = :user_id",
            {"id": webhook_id, "user_id": user.uid},
        )
    return _back(username, "toggled")


@discord_webhooks_blueprint.route("/<int:webhook_id>/test", methods=["POST"])
@login_required
def test(username, webhook_id):
    user = User.query.filter_by(username=username).first()
    with pg_session() as pg:
        row = pg.execute(
            "SELECT url FROM user_discord_webhooks WHERE id = :id AND user_id = :user_id",
            {"id": webhook_id, "user_id": user.uid},
        ).fetchone()
    if row is None:
        return _back(username, "test_failed")
    sent = post_webhook_message(
        row["url"], "✅ Trainlog is connected to this channel.", username="Trainlog"
    )
    return _back(username, "tested" if sent else "test_failed")
