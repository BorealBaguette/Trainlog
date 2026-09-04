"""Minimal Discord bot REST helpers for automated premium tier role sync.

No gateway/event-loop process is used — Trainlog is a request/response Flask
app, and the only capability needed is granting/revoking guild roles, which
the Discord REST API supports directly via bot-token auth.
"""

import json
import logging
import time

import requests

from py.utils import load_config

# Shares the dedicated "bmc" logger (see logging.conf / src/api/bmc.py) since
# Discord role sync is always triggered by a BMC event or the manual toggle.
logger = logging.getLogger("bmc")

DISCORD_API = "https://discord.com/api/v10"

# BMC membership tier slug -> discord.<key> config entry holding that tier's role id.
TIER_ROLE_KEYS = {
    "trainlogger": "trainlogger_role_id",
    "first_class": "first_class_role_id",
    "rail_baron": "rail_baron_role_id",
}


def _set_role(discord_id: str, role_id: str, grant: bool) -> bool:
    config = load_config().get("discord", {})
    bot_token, guild_id = config.get("bot_token"), config.get("guild_id")
    if not (bot_token and guild_id):
        logger.warning("Discord bot not configured; skipping role sync for %s", discord_id)
        return False

    url = f"{DISCORD_API}/guilds/{guild_id}/members/{discord_id}/roles/{role_id}"
    method = requests.put if grant else requests.delete
    try:
        response = method(
            url,
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=10,
        )
        if response.status_code == 204:
            logger.info(
                "Discord role %s OK: discord_id=%s role_id=%s",
                "grant" if grant else "revoke", discord_id, role_id,
            )
            return True
        logger.warning(
            "Discord role %s failed for %s: %s %s",
            "grant" if grant else "revoke",
            discord_id,
            response.status_code,
            response.text,
        )
        return False
    except requests.RequestException as e:
        logger.warning("Discord API error while syncing role for %s: %s", discord_id, e)
        return False


def sync_discord_tier(user, tier: str | None) -> None:
    """Grant the Discord role for `tier` and revoke every other known tier role,
    so a user only ever holds one premium tier role at a time. tier=None revokes
    all of them (no active premium). No-ops if Discord isn't linked.

    Never raises — a Discord outage must not break the caller (a webhook handler
    or the manual admin toggle route).
    """
    if not user.discord_id:
        logger.info("Discord sync skipped for uid=%s: no linked discord_id", user.uid)
        return
    config = load_config().get("discord", {})
    target_key = TIER_ROLE_KEYS.get(tier)
    logger.info(
        "Discord sync: uid=%s discord_id=%s target_tier=%r (config key=%s)",
        user.uid, user.discord_id, tier, target_key,
    )
    for key in TIER_ROLE_KEYS.values():
        role_id = config.get(key)
        if not role_id:
            logger.warning("Discord sync: no role id configured for %s, skipping", key)
            continue
        _set_role(user.discord_id, role_id, grant=(key == target_key))


# Guild display names, cached so a tick that posts several trips does not ask
# Discord for the same member again. Keyed by discord_id -> (expires, name,
# avatar_url). Per-process, so every gunicorn worker keeps its own; the point is
# to avoid a burst of identical calls, not to be a shared cache.
_MEMBER_CACHE = {}
_MEMBER_TTL = 3600


def guild_display_name(discord_id: str):
    """(display name, avatar url) for a member of the Trainlog guild.

    What people know each other by in the server is the nickname they set
    there, which is often nothing like the account's username — so a post made
    on someone's behalf should carry the nickname, falling back to the account
    display name and then the username, the same order Discord itself shows.

    Returns (None, None) when the member cannot be looked up (bot not
    configured, member left the guild, Discord unreachable); the caller then
    posts under whatever name it already had. Never raises.
    """
    cached = _MEMBER_CACHE.get(discord_id)
    if cached and cached[0] > time.time():
        return cached[1], cached[2]

    config = load_config().get("discord", {})
    bot_token, guild_id = config.get("bot_token"), config.get("guild_id")
    if not (bot_token and guild_id):
        return None, None

    try:
        response = requests.get(
            f"{DISCORD_API}/guilds/{guild_id}/members/{discord_id}",
            headers={"Authorization": f"Bot {bot_token}"},
            timeout=10,
        )
        if response.status_code != 200:
            logger.info(
                "Discord member lookup for %s: %s %s",
                discord_id, response.status_code, response.text,
            )
            return None, None
        member = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Discord API error while looking up member %s: %s", discord_id, e)
        return None, None

    user = member.get("user") or {}
    name = member.get("nick") or user.get("global_name") or user.get("username")

    # A per-guild avatar overrides the account one, and lives under a different
    # path. Either may be unset, in which case Discord's default is used.
    if member.get("avatar"):
        avatar = (
            f"https://cdn.discordapp.com/guilds/{guild_id}/users/{discord_id}"
            f"/avatars/{member['avatar']}.png"
        )
    elif user.get("avatar"):
        avatar = f"https://cdn.discordapp.com/avatars/{discord_id}/{user['avatar']}.png"
    else:
        avatar = None

    _MEMBER_CACHE[discord_id] = (time.time() + _MEMBER_TTL, name, avatar)
    return name, avatar


def post_webhook_message(
    webhook_url: str, content: str, username: str = None, file=None,
    avatar_url: str = None,
):
    """Post a plain-text message through a channel webhook.

    Webhooks are used rather than the bot token because they are bound to their
    channel and need no guild membership or channel permission — the same
    reason the BMC and feature-request notifications use them. ``username`` and
    ``avatar_url`` override the displayed author per message, so a trip can be
    posted under the name and face of whoever took it.

    Returns the message id on success, ``False`` when Discord answered with an
    error (nothing was posted, so retrying is safe), and ``None`` when we never
    got an answer — a timeout may well have posted it, and retrying on None
    risks posting twice.

    Never raises: a Discord outage must not take down whatever asked for the
    post (the trip announcer polls in a loop and tries again next tick).
    """
    if not webhook_url:
        logger.warning("No trips webhook configured; skipping message")
        return False

    # flags 1<<2 is SUPPRESS_EMBEDS: the trip link would otherwise unfurl into
    # a link preview underneath, which says less than the card already attached.
    payload = {
        "content": content,
        "allowed_mentions": {"parse": []},
        "flags": 4,
    }
    if username:
        payload["username"] = username
    if avatar_url:
        payload["avatar_url"] = avatar_url

    if file:
        # An attachment has to go as multipart, with the rest of the message
        # riding along in payload_json.
        kwargs = {
            "data": {"payload_json": json.dumps(payload)},
            "files": {"files[0]": (file[0], file[1], "image/png")},
        }
    else:
        kwargs = {"json": payload}

    try:
        # wait=true makes Discord return the created message instead of 204,
        # which is the only way to learn its id.
        response = requests.post(
            webhook_url, params={"wait": "true"}, timeout=20, **kwargs
        )
        if response.status_code in (200, 201):
            return response.json().get("id")
        logger.warning(
            "Discord webhook message failed: %s %s",
            response.status_code, response.text,
        )
        return False
    except requests.RequestException as e:
        logger.warning("Discord API error while posting via webhook: %s", e)
    return None


def delete_webhook_message(webhook_url: str, message_id: str) -> bool:
    """Delete a message this webhook posted.

    A webhook may delete its own messages with nothing but its URL, which is
    what lets a trip announcement be taken down without a bot token or any
    channel permission. A message that is already gone counts as deleted: what
    matters is that it is not in the channel, not who removed it.

    Never raises. Returns False whenever the message may still be up, so the
    caller can keep its record of it and let the user try again.
    """
    if not webhook_url:
        logger.warning(
            "No trips webhook configured; cannot delete message %s", message_id
        )
        return False

    try:
        response = requests.delete(f"{webhook_url}/messages/{message_id}", timeout=20)
        if response.status_code in (204, 404):
            return True
        logger.warning(
            "Discord webhook message delete failed: %s %s",
            response.status_code, response.text,
        )
    except requests.RequestException as e:
        logger.warning("Discord API error while deleting webhook message: %s", e)
    return False
