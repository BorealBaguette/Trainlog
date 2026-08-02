"""
Forge a correctly-signed BMC membership webhook and POST it to a local Trainlog
instance, for testing the BMC integration (src/api/bmc.py) without needing BMC
to actually reach your machine.

BMC's own dashboard "Send test event" always sends canned sample data (e.g.
membership_level_name="Basic"), which won't exercise the real tier-matching
logic. This script builds a payload shaped like a real event and signs it with
the same HMAC-SHA256 secret BMC uses, so the request is indistinguishable from
a real one as far as src/api/bmc.py is concerned.

Usage (run from the repo root, with the dev server running):

    python -m scripts.simulate_bmc_webhook --email you@example.com --tier trainlogger
    python -m scripts.simulate_bmc_webhook --email you@example.com --event cancelled
    python -m scripts.simulate_bmc_webhook --email you@example.com --tier show_your_love --duration year --amount 10

Reads the signing secret from config.yaml (bmc.webhooks) — the same file the
running app reads, so no separate configuration is needed.
"""

import argparse
import hashlib
import hmac
import json
import time

import requests

from py.utils import load_config

# tier slug -> (membership_level_name as BMC would send it, default monthly amount)
TIERS = {
    "show_your_love": ("Show your love", 1),
    "trainlogger": ("Trainlogger", 5),
    "first_class": ("1st Class Logger", 15),
    "rail_baron": ("Rail Baron", 30),
}

EVENT_TYPES = {
    "started": "membership.started",
    "updated": "membership.updated",
    "cancelled": "membership.cancelled",
    "paused": "membership.paused",
}


def build_payload(event: str, email: str, tier: str, amount: float, currency: str, duration: str, supporter_id: int) -> dict:
    level_name, default_amount = TIERS[tier]
    return {
        "event_id": int(time.time()),
        "type": EVENT_TYPES[event],
        "live_mode": False,
        "created": int(time.time()),
        "attempt": 1,
        "data": {
            "id": 999999,
            "object": "membership",
            "psp_id": "sub_simulated",
            "duration_type": duration,
            "membership_level_id": 0,
            "membership_level_name": level_name,
            "status": "active" if event in ("started", "updated") else event,
            "canceled": "true" if event == "cancelled" else "false",
            "cancel_at_period_end": "false",
            "paused": "true" if event == "paused" else "false",
            "supporter_name": "Test Supporter",
            "supporter_id": supporter_id,
            "supporter_email": email,
            "amount": amount if amount is not None else default_amount,
            "currency": currency,
            "started_at": int(time.time()),
            "current_period_start": int(time.time()),
            "current_period_end": int(time.time()) + 30 * 86400,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True, help="supporter_email — must match a Trainlog account's login email")
    parser.add_argument("--tier", choices=TIERS, default="trainlogger", help="which tier's name/price to simulate")
    parser.add_argument("--event", choices=EVENT_TYPES, default="started", help="membership.<event>")
    parser.add_argument("--amount", type=float, default=None, help="override the tier's default amount")
    parser.add_argument("--currency", default="EUR")
    parser.add_argument("--duration", choices=["month", "year"], default="month")
    parser.add_argument("--url", default="http://localhost:5000/api/bmc/webhook")
    parser.add_argument(
        "--supporter-id", type=int, default=None,
        help="BMC's stable per-supporter id. Defaults to a hash of --email so repeated "
             "runs for the same email reuse the same id, like a real supporter would. "
             "Override this (keeping --email different) to test bmc_supporter_id pinning "
             "surviving an email change.",
    )
    args = parser.parse_args()

    supporter_id = args.supporter_id if args.supporter_id is not None else (abs(hash(args.email)) % 100000)
    secret = load_config()["bmc"]["webhooks"]
    payload = build_payload(args.event, args.email, args.tier, args.amount, args.currency, args.duration, supporter_id)
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    print(json.dumps(payload, indent=2))
    response = requests.post(
        args.url,
        data=body,
        headers={"Content-Type": "application/json", "x-signature-sha256": signature},
        timeout=10,
    )
    print(f"\n-> {response.status_code} {response.text}")


if __name__ == "__main__":
    main()
