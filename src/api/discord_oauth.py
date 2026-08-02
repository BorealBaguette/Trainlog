"""src/api/discord_oauth.py — "Connect Discord" account-linking flow.

Lets a logged-in Trainlog user link their Discord account (storing
User.discord_id) so the premium role can be granted/revoked automatically.
Uses the standard OAuth2 authorization-code flow with the `identify` scope.
"""

import logging
import secrets

import requests
from flask import Blueprint, redirect, request, session, url_for

from py.utils import load_config
from src.discord_bot import sync_discord_tier
from src.users import User, authDb
from src.utils import external_url

# Dedicated "bmc" logger (see logging.conf): writes to logs/bmc.log instead of
# the general console/app log, consistent with src/api/bmc.py and discord_bot.py.
logger = logging.getLogger("bmc")

discord_oauth_blueprint = Blueprint("discord_oauth", __name__, url_prefix="/discord")

DISCORD_AUTHORIZE_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API = "https://discord.com/api/v10"


def _current_user() -> User | None:
    username = session.get("logged_in")
    user_id = session.get("logged_in_user_id")
    if not (username and user_id and session.get(username)):
        return None
    return User.query.filter_by(uid=user_id).first()


@discord_oauth_blueprint.route("/connect", methods=["GET"])
def connect():
    user = _current_user()
    if not user:
        return redirect(url_for("login", next=request.path))

    config = load_config().get("discord", {})
    client_id = config.get("client_id")
    if not client_id:
        return "Discord integration is not configured", 500

    state = secrets.token_urlsafe(24)
    session["discord_oauth_state"] = state

    redirect_uri = external_url("discord_oauth.callback")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    query = "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
    return redirect(f"{DISCORD_AUTHORIZE_URL}?{query}")


@discord_oauth_blueprint.route("/callback", methods=["GET"])
def callback():
    user = _current_user()
    if not user:
        return redirect(url_for("login"))

    expected_state = session.pop("discord_oauth_state", None)
    state = request.args.get("state")
    code = request.args.get("code")
    if not code or not state or state != expected_state:
        return "Invalid or expired Discord authorization request", 400

    config = load_config().get("discord", {})
    client_id = config.get("client_id")
    client_secret = config.get("client_secret")
    redirect_uri = external_url("discord_oauth.callback")

    try:
        token_response = requests.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]

        me_response = requests.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        me_response.raise_for_status()
        me = me_response.json()
        discord_id = me["id"]
        discord_username = me.get("username")
    except (requests.RequestException, KeyError):
        logger.exception("Discord OAuth exchange failed")
        return "Failed to connect Discord account", 502

    user.discord_id = discord_id
    user.discord_username = discord_username
    authDb.session.commit()

    # Only actively sync roles here if a real BMC webhook has already pinned
    # bmc_supporter_id — that's our one signal that premium_tier is trustworthy.
    # Without it (premium set manually, or predating this system, or just not
    # matched yet), premium_tier can be stale/None even for a legitimate premium
    # user — syncing blindly on first connect would strip any Discord role they
    # already hold (hand-assigned or from before this bot existed) the moment
    # they link their account. Leave it alone until a real webhook confirms it.
    if user.premium and user.bmc_supporter_id:
        sync_discord_tier(user, user.premium_tier)
    elif user.premium:
        logger.info(
            "Discord connected for uid=%s (%s) but bmc_supporter_id is unset — "
            "not syncing roles on connect, leaving any existing role untouched "
            "until a BMC webhook confirms the real tier",
            user.uid, user.username,
        )

    return redirect(url_for("user_settings", username=user.username))


@discord_oauth_blueprint.route("/unlink", methods=["POST"])
def unlink():
    user = _current_user()
    if not user:
        return redirect(url_for("login"))

    if user.discord_id:
        sync_discord_tier(user, None)
        user.discord_id = None
        user.discord_username = None
        authDb.session.commit()

    return redirect(url_for("user_settings", username=user.username))
