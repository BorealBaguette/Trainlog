import logging
import socket

import requests

from py.utils import load_config

logger = logging.getLogger(__name__)

HOSTNAME = socket.gethostname()


def report_error(
    subject: str,
    message: str,
    *,
    url: str = "",
    user: str = "",
) -> None:
    """Fire-and-forget error report. Never raises; never blocks long."""
    cfg = load_config().get("error_collector") or {}
    collector_url = cfg.get("url")
    collector_token = cfg.get("token")
    if not collector_url or not collector_token:
        return
    try:
        requests.post(
            collector_url,
            json={
                "subject": subject,
                "message": message,
                "url": url,
                "user": user,
                "host": HOSTNAME,
            },
            headers={"X-Auth-Token": collector_token},
            timeout=(3, 3),
        )
    except requests.RequestException as e:
        logger.warning("Failed to report error to collector: %s", e)
