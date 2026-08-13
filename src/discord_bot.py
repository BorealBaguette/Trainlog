"""Minimal Discord bot REST helpers for automated premium tier role sync.

No gateway/event-loop process is used — Trainlog is a request/response Flask
app, and the only capability needed is granting/revoking guild roles, which
the Discord REST API supports directly via bot-token auth.
"""

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
