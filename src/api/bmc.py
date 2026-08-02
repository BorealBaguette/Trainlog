"""src/api/bmc.py — Buy Me a Coffee membership webhook.

Automates premium status + Discord tier-role sync from BMC membership events,
replacing the previous fully-manual workflow. BMC remains the payment
processor; this only reacts to its webhook notifications.

Signature verification: BMC signs each request with HMAC-SHA256 over the raw
body, sent in the `x-signature-sha256` header, using the secret configured at
buymeacoffee.com/webhooks (stored as `bmc.webhooks` in config.yaml).

Every inbound webhook and every resulting action is logged at INFO so the
whole flow (received -> resolved -> applied) is visible in the server logs.
"""

import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from py.utils import load_config
from src.discord_bot import sync_discord_tier
from src.users import PendingBmcEvent, User, authDb
from src.utils import external_url, getUser, lang, owner_required, post_to_discord, sendEmail

# Dedicated "bmc" logger (see logging.conf): writes to logs/bmc.log instead of
# the general console/app log, so BMC/Discord/premium activity is isolated.
logger = logging.getLogger("bmc")

bmc_blueprint = Blueprint("bmc", __name__, url_prefix="/api/bmc")

# Event types that (re)apply a membership's current tier.
GRANT_EVENTS = {"membership.started", "membership.updated"}
# Event types that end a membership outright.
REVOKE_EVENTS = {"membership.cancelled", "membership.paused"}

# BMC membership_level_name (case-insensitive) -> internal tier slug. Only these
# three paid tiers grant premium; "Show your love" is a plain donation tier and
# deliberately has no entry here, regardless of its price or billing period.
TIER_BY_LEVEL_NAME = {
    "trainlogger": "trainlogger",
    "1st class logger": "first_class",
    "rail baron": "rail_baron",
}


def _resolve_tier(data: dict) -> str | None:
    """Resolve the tier for a GRANT_EVENTS (.started/.updated) payload.

    Confirmed from a real cancel-at-period-end test: BMC sends that as
    membership.updated with canceled still "false" (only cancel_at_period_end
    flips) — membership_level_name is unchanged, and this correctly keeps
    access until the period truly ends. But since BMC appears to prefer
    .updated for state changes generally, the *true* termination might also
    arrive as .updated rather than a dedicated .cancelled event — so don't
    trust membership_level_name blindly if canceled is explicitly "true".
    (Not also gating on `status` here: we don't have confirmed knowledge of
    every value BMC might send there, e.g. a payment-retry state that
    shouldn't revoke — canceled=="true" is the one field with a directly
    observed, unambiguous meaning.)
    """
    if data.get("canceled") == "true":
        return None
    name = (data.get("membership_level_name") or "").strip().lower()
    return TIER_BY_LEVEL_NAME.get(name)


def _verify_signature(payload: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _find_supporter_user(data: dict) -> User | None:
    """Resolve the BMC supporter to a Trainlog account.

    Checks the pinned bmc_supporter_id first (set the first time this supporter
    is ever matched) so later events — a cancellation, say — still find the
    account even if its Trainlog login email has since changed away from
    whatever BMC has on file. Falls back to email for a first-time match.
    """
    supporter_id = data.get("supporter_id")
    if supporter_id:
        user = User.query.filter_by(bmc_supporter_id=str(supporter_id)).first()
        if user:
            return user

    email = data.get("supporter_email")
    if not email:
        return None
    email = email.strip().lower()
    return User.query.filter(authDb.func.lower(User.email) == email).first()


def _notify_owner(event_type: str, data: dict, tier: str | None, user: User | None) -> None:
    """Post a summary of this webhook to a private Discord channel (config key
    `discord.bmc_activity`) so it can be watched without exposing anything about
    the BMC integration in the product itself. Never raises — best-effort only."""
    level = data.get("membership_level_name") or "—"
    if user:
        title = f"BMC: {event_type} — matched {user.username}"
        color = 0x2ECC71 if tier else 0xE67E22
    else:
        title = f"BMC: {event_type} — no match"
        color = 0xE74C3C
    try:
        ok = post_to_discord(
            "bmc_activity",
            title,
            f"tier={tier or 'none'}",
            fields=[
                {"name": "Level", "value": level, "inline": True},
                {"name": "Trainlog user", "value": user.username if user else "unmatched", "inline": True},
            ],
            color=color,
        )
        logger.info("BMC: owner Discord notification %s", "sent" if ok else "FAILED (see above for details, if any)")
    except Exception:
        logger.exception("BMC: failed to post owner Discord notification")


def apply_membership_status(user: User, tier: str | None, supporter_id: str | None = None) -> None:
    """Set premium + tier + sync Discord for a resolved user. tier=None means no
    active paid tier (revoke). Also pins bmc_supporter_id the first time it's
    seen, so future events keep finding this account regardless of email
    changes (see _find_supporter_user). Shared by the webhook handler,
    pending-event reconciliation, the claim link, and the owner's manual assign."""
    grant = tier is not None
    changed = False

    if supporter_id and user.bmc_supporter_id != str(supporter_id):
        logger.info(
            "BMC: pinning bmc_supporter_id=%s to uid=%s (%s)",
            supporter_id, user.uid, user.username,
        )
        user.bmc_supporter_id = str(supporter_id)
        changed = True

    if grant and user.premium_cancel_at is not None:
        # A new grant (renewal, re-subscription) supersedes any earlier
        # cancellation that was only flagged, not yet manually revoked.
        logger.info(
            "BMC: clearing pending cancellation flag for uid=%s (%s) — new grant received",
            user.uid, user.username,
        )
        user.premium_cancel_at = None
        changed = True

    if user.premium != grant or user.premium_tier != tier:
        logger.info(
            "BMC: updating uid=%s (%s): premium %s->%s, tier %r->%r",
            user.uid, user.username, user.premium, grant, user.premium_tier, tier,
        )
        user.premium = grant
        user.premium_tier = tier
        changed = True
    else:
        logger.info(
            "BMC: uid=%s (%s) already premium=%s tier=%r, no status change",
            user.uid, user.username, grant, tier,
        )

    if changed:
        authDb.session.commit()
    logger.info("BMC: syncing Discord tier=%r for uid=%s", tier, user.uid)
    sync_discord_tier(user, tier)


def _parse_period_end(data: dict) -> datetime | None:
    raw = data.get("current_period_end")
    if not raw:
        return None
    try:
        # Naive on purpose: SQLite/SQLAlchemy strips tzinfo on the round trip
        # through the DB anyway, so store it naive-but-UTC-valued from the
        # start rather than relying on that implicit stripping. Every read-side
        # comparison (getStalePremiumCount, getAdminUsersData) must do the same.
        return datetime.fromtimestamp(float(raw), UTC).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def flag_pending_cancellation(user: User, data: dict) -> None:
    """A cancel/pause event arrived for a currently-premium, matched user.

    Policy: honor already-paid-for access — don't revoke immediately (confirmed
    via a real event that membership.cancelled fires right away on cancellation,
    not at period end). Just record the paid-through date so it's visible on
    /admin and can be revoked manually via /toggle_role once appropriate. Never
    touches premium/premium_tier or Discord roles itself."""
    period_end = _parse_period_end(data)
    if user.premium_cancel_at != period_end:
        user.premium_cancel_at = period_end
        authDb.session.commit()
    logger.info(
        "BMC: uid=%s (%s) cancellation flagged (paid through %s) — access "
        "continues, revoke manually via /toggle_role when appropriate",
        user.uid, user.username, period_end,
    )


def _apply_pending_events_for_email(email: str, user: User) -> bool:
    """Apply the most recent pending BMC event for `email` to `user`, then clear
    every pending row for that email. Returns False if there were none. Shared
    by the email-change reconciliation, the claim link, and the owner's manual
    assign action — the only difference between them is how `user` was found."""
    email = (email or "").strip().lower()
    if not email:
        return False
    events = (
        PendingBmcEvent.query.filter_by(supporter_email=email)
        .order_by(PendingBmcEvent.created_at.desc())
        .all()
    )
    if not events:
        return False
    latest = events[0]
    logger.info(
        "BMC: applying %d pending event(s) for %s to uid=%s (%s); "
        "using most recent (event_type=%s, grant=%s, tier=%r)",
        len(events), email, user.uid, user.username,
        latest.event_type, latest.grant, latest.tier,
    )
    apply_membership_status(
        user,
        tier=latest.tier if latest.grant else None,
        supporter_id=latest.supporter_id,
    )
    for event in events:
        authDb.session.delete(event)
    authDb.session.commit()
    return True


def reconcile_pending_events(user: User) -> None:
    """Apply any BMC events that arrived before `user.email` matched the
    supporter's BMC email (e.g. before an email change was confirmed)."""
    _apply_pending_events_for_email(user.email, user)


def _current_user() -> User | None:
    username = session.get("logged_in")
    user_id = session.get("logged_in_user_id")
    if not (username and user_id and session.get(username)):
        return None
    return User.query.filter_by(uid=user_id).first()


def _send_claim_email(email: str, token: str) -> None:
    """English-only: we have no locale for a supporter who isn't a Trainlog
    account yet (or isn't logged in as one), so there's no language to pick."""
    link = external_url("bmc.claim", token=token)
    sendEmail(
        email,
        "Trainlog: link your membership",
        "Hello,<br><br>"
        "We received a Buy Me a Coffee membership under this email address, but "
        "couldn't find a matching Trainlog account.<br><br>"
        "If this is you, log in to Trainlog (or sign up first if you don't have "
        f'an account yet), then click this link to activate your membership: '
        f'<a href="{link}">{link}</a><br><br>'
        "If you weren't expecting this, you can safely ignore this email.<br><br>"
        "Thanks,<br>"
        "The Trainlog team",
    )


@bmc_blueprint.route("/claim/<token>", methods=["GET"])
def claim(token):
    """Land here from the claim email. Applies every pending event sharing this
    token to whichever Trainlog account is logged in when it's clicked."""
    match = PendingBmcEvent.query.filter_by(claim_token=token).first()
    if not match:
        return "This link is invalid or has already been used.", 404

    user = _current_user()
    if not user:
        return redirect(url_for("login", next=request.path))

    applied = _apply_pending_events_for_email(match.supporter_email, user)
    if applied:
        flash("Your Buy Me a Coffee membership has been linked to your account.", "success")
    else:
        flash("This link has already been used.", "error")
    return redirect(url_for("user_settings", username=user.username))


@bmc_blueprint.route("/assign", methods=["POST"])
@owner_required
def assign():
    """Owner-only manual bind: apply a pending email's events to a chosen
    Trainlog account without requiring that user to change their login email
    or click the claim link themselves."""
    email = (request.form.get("email") or "").strip().lower()
    username = (request.form.get("username") or "").strip()
    user = User.query.filter_by(username=username).first()
    if not user:
        flash(f"No Trainlog user named '{username}'", "error")
        return redirect(url_for("bmc.pending"))

    if _apply_pending_events_for_email(email, user):
        flash(f"Assigned {email}'s pending membership to {user.username}.", "success")
    else:
        flash(f"No pending events found for {email}.", "error")
    return redirect(url_for("bmc.pending"))


@bmc_blueprint.route("/dismiss", methods=["POST"])
@owner_required
def dismiss():
    """Owner-only: discard every pending event for an email without applying
    it to anyone — e.g. a test/junk address that will never resolve on its own."""
    email = (request.form.get("email") or "").strip().lower()
    events = PendingBmcEvent.query.filter_by(supporter_email=email).all()
    if not events:
        flash(f"No pending events found for {email}.", "error")
        return redirect(url_for("bmc.pending"))

    for event in events:
        authDb.session.delete(event)
    authDb.session.commit()
    logger.info("BMC: dismissed %d pending event(s) for %s (owner action)", len(events), email)
    flash(f"Dismissed {len(events)} pending event(s) for {email}.", "success")
    return redirect(url_for("bmc.pending"))


@bmc_blueprint.route("/pending", methods=["GET"])
@owner_required
def pending():
    """Owner-only, unlinked-from-any-nav view of BMC webhooks that couldn't be
    matched to a Trainlog account yet (see reconcile_pending_events)."""
    events = PendingBmcEvent.query.order_by(PendingBmcEvent.created_at.desc()).all()
    return render_template(
        "admin/bmc_pending.html",
        events=events,
        username=getUser(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


def _process_event(event: dict) -> dict:
    """Core BMC event handling, shared by the real signed webhook and the
    owner's manual /replay tool. Never raises — callers get a result dict back
    regardless of outcome; errors are logged and reported in the dict instead."""
    try:
        event_type = event.get("type")
        event_id = event.get("event_id")
        live_mode = event.get("live_mode")
        data = event.get("data") or {}

        # Deliberately not logging the whole `data` dict: it can carry a
        # supporter-written free-text note (support_note/supporter_feedback).
        logger.info(
            "BMC event received: type=%s event_id=%s live_mode=%s "
            "supporter_email=%s level=%r amount=%s currency=%s duration=%s",
            event_type, event_id, live_mode,
            data.get("supporter_email"), data.get("membership_level_name"),
            data.get("amount"), data.get("currency"), data.get("duration_type"),
        )

        if event_type not in GRANT_EVENTS and event_type not in REVOKE_EVENTS:
            logger.info("BMC event: ignoring unhandled event type=%s", event_type)
            return {"success": True, "ignored": event_type}

        user = _find_supporter_user(data)

        if event_type in REVOKE_EVENTS:
            if user:
                flag_pending_cancellation(user, data)
                _notify_owner(
                    f"{event_type} — access continues (paid through period end), revoke manually",
                    data, user.premium_tier, user=user,
                )
                return {"success": True, "matched": True, "flagged": True, "uid": user.uid}
            # Fall through to the shared unmatched-queue logic below, exactly
            # like a grant event with tier=None — there's no active access to
            # protect for an account that was never matched in the first place.

        # A downgrade to "Show your love" (or any unrecognized level) arrives as
        # membership.updated, not .cancelled — resolving to tier=None here
        # correctly revokes premium via apply_membership_status. Revoke events
        # also resolve to tier=None here (never through _resolve_tier).
        tier = _resolve_tier(data) if event_type in GRANT_EVENTS else None
        grant = tier is not None
        logger.info(
            "BMC event: event_id=%s resolved to tier=%r (grant=%s)",
            event_id, tier, grant,
        )

        if not user:
            email = (data.get("supporter_email") or "").strip().lower()
            if email:
                existing = (
                    PendingBmcEvent.query.filter_by(supporter_email=email)
                    .order_by(PendingBmcEvent.created_at.desc())
                    .first()
                )
                token = existing.claim_token if existing and existing.claim_token else secrets.token_urlsafe(32)
                authDb.session.add(
                    PendingBmcEvent(
                        supporter_email=email,
                        supporter_id=str(data["supporter_id"]) if data.get("supporter_id") else None,
                        grant=grant,
                        tier=tier,
                        event_type=event_type,
                        claim_token=token,
                    )
                )
                authDb.session.commit()
                logger.warning(
                    "BMC event: event_id=%s no Trainlog user matches email=%s — "
                    "queued as a pending event, to be replayed if that email is later confirmed",
                    event_id, email,
                )
                # Only email on the first pending event for this address (grant
                # events only — no point inviting someone to "claim" a cancellation).
                if not existing and grant:
                    _send_claim_email(email, token)
                    logger.info("BMC event: sent claim email to %s", email)
            else:
                logger.warning(
                    "BMC event: event_id=%s has no supporter_email, cannot match or queue",
                    event_id,
                )
            _notify_owner(event_type, data, tier, user=None)
            return {"success": True, "matched": False}

        logger.info(
            "BMC event: event_id=%s matched uid=%s (%s, %s)",
            event_id, user.uid, user.username, user.email,
        )
        apply_membership_status(user, tier=tier, supporter_id=data.get("supporter_id"))
        _notify_owner(event_type, data, tier, user=user)

        return {"success": True, "matched": True, "uid": user.uid, "premium": grant, "tier": tier}
    except Exception:
        logger.exception("BMC event: unhandled error while processing")
        return {"success": False}


@bmc_blueprint.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_data()
    secret = load_config().get("bmc", {}).get("webhooks")
    signature = request.headers.get("x-signature-sha256")
    if not secret or not _verify_signature(payload, signature, secret):
        logger.warning(
            "BMC webhook: signature verification failed (signature present=%s, %d byte body)",
            bool(signature), len(payload),
        )
        return jsonify(success=False, error="Invalid signature"), 400

    try:
        event = request.get_json(force=True, silent=False)
    except Exception:
        logger.exception("BMC webhook: request body is not valid JSON")
        return jsonify(success=False, error="Invalid JSON"), 400

    return jsonify(_process_event(event)), 200


@bmc_blueprint.route("/replay", methods=["POST"])
@owner_required
def replay():
    """Owner-only: paste a raw event JSON (e.g. copied from BMC's own event
    delivery log) and process it exactly as if it had arrived as a real signed
    webhook. No signature check — the owner_required session is the auth here.
    For backfilling events that accumulated before the webhook was configured,
    or any delivery that failed and isn't worth waiting on a resend for."""
    raw = request.form.get("payload", "")
    try:
        event = json.loads(raw)
    except (TypeError, ValueError) as e:
        flash(f"Invalid JSON: {e}", "error")
        return redirect(url_for("bmc.pending"))

    if not isinstance(event, dict):
        flash("Payload must be a single JSON object (one event, not a list).", "error")
        return redirect(url_for("bmc.pending"))

    logger.info("BMC: replaying manually-submitted event (owner action): event_id=%s type=%s", event.get("event_id"), event.get("type"))
    result = _process_event(event)
    flash(f"Replay result: {result}", "success" if result.get("success") else "error")
    return redirect(url_for("bmc.pending"))
