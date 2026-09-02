"""Minimal Discord bot REST helpers for automated premium tier role sync.

No gateway/event-loop process is used — Trainlog is a request/response Flask
app, and the only capability needed is granting/revoking guild roles, which
the Discord REST API supports directly via bot-token auth.
"""

import json
import logging

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


def post_webhook_message(
    webhook_url: str, content: str, username: str = None, file=None
):
    """Post a plain-text message through a channel webhook.

    Webhooks are used rather than the bot token because they are bound to their
    channel and need no guild membership or channel permission — the same
    reason the BMC and feature-request notifications use them. ``username``
    overrides the displayed author per message, so a trip can be posted under
    the name of whoever took it.

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
