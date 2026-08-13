from datetime import UTC, datetime

from flask_sqlalchemy import SQLAlchemy

authDb = SQLAlchemy()


class User(authDb.Model):
    uid = authDb.Column(authDb.Integer, primary_key=True)
    username = authDb.Column(authDb.String(100), unique=True, nullable=False)
    email = authDb.Column(authDb.String(100), unique=True, nullable=False)
    pass_hash = authDb.Column(authDb.String(100), nullable=False)
    lang = authDb.Column(authDb.String(2), nullable=False, default="en")
    share_level = authDb.Column(authDb.Integer, nullable=False, default=0)
    leaderboard = authDb.Column(authDb.Boolean, nullable=False, default=False)
    creation_date = authDb.Column(
        authDb.DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    last_login = authDb.Column(
        authDb.DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    admin = authDb.Column(authDb.Boolean, nullable=False, default=False)
    alpha = authDb.Column(authDb.Boolean, nullable=False, default=False)
    translator = authDb.Column(authDb.Boolean, nullable=False, default=False)
    user_currency = authDb.Column(authDb.String(3), nullable=False, default="EUR")
    friend_search = authDb.Column(authDb.Boolean, nullable=False, default=True)
    colorblind = authDb.Column(authDb.Boolean, nullable=False, default=False)
    reset_token = authDb.Column(authDb.String(100), default="")
    # Per-user secret token for the GPSLogger GPX ingest endpoint
    # (/api/gps/<token>/upload). Scoped to GPS ingest only; regenerable.
    gps_token = authDb.Column(authDb.String(100), default="")
    # Per-user secret token for the MCP server (/mcp?api_key=<token>), letting an
    # external AI manage this user's trips. Regenerable; revokes on regenerate.
    mcp_token = authDb.Column(authDb.String(100), default="")
    default_landing = authDb.Column(authDb.String(20), nullable=False, default="map")
    appear_on_global = authDb.Column(authDb.Boolean, nullable=False, default=False)
    tileserver = authDb.Column(authDb.String(50), nullable=False, default="default")
    globe = authDb.Column(authDb.Boolean, nullable=False, default=False)
    premium = authDb.Column(authDb.Boolean, nullable=False, default=False)
    # Which BMC membership tier ("trainlogger", "first_class", "rail_baron") is
    # currently active, if any. Drives which Discord role sync_discord_tier grants
    # — kept in sync with `premium` by apply_membership_status (src/api/bmc.py).
    premium_tier = authDb.Column(authDb.String(20), nullable=True)
    # BMC's stable per-supporter id, pinned the first time a webhook is matched
    # to this user (by email, claim link, or manual assign). Checked before email
    # on later events, so a subsequent cancellation still finds this account even
    # if the user has since changed their Trainlog login email away from the
    # address BMC has on file for them.
    bmc_supporter_id = authDb.Column(authDb.String(30), nullable=True, index=True)
    # Set when a membership.cancelled/.paused webhook arrives for a currently
    # premium user. Policy: honor already-paid-for access — don't revoke
    # immediately, just record the paid-through date here so it's visible on
    # /admin for manual revocation once it's actually passed. Cleared by a new
    # grant event (re-subscription) or a manual revoke via /toggle_role.
    premium_cancel_at = authDb.Column(authDb.DateTime, nullable=True)
    feature_admin = authDb.Column(authDb.Boolean, nullable=False, default=False)
    # Premium-only: render flight tracks as a 3D altitude profile on trip pages.
    flight_3d = authDb.Column(authDb.Boolean, nullable=False, default=False)
    # Premium-only: while a flight is in the air, draw its flown-so-far track from FR24
    # instead of a geodesic. Off by default even for premium, because broadcasting a
    # real-time position is a materially different disclosure from a historical log.
    live_tracking = authDb.Column(authDb.Boolean, nullable=False, default=False)
    # Discord user id, set via the /discord/connect OAuth flow. Used to grant/revoke
    # the premium role automatically when membership status changes.
    discord_id = authDb.Column(authDb.String(30), nullable=True)
    # Discord's @username at the time of linking (display only — not re-synced,
    # so it can go stale if they rename on Discord; discord_id is the real key).
    discord_username = authDb.Column(authDb.String(50), nullable=True)
    # Email change requested but not yet confirmed. `email` itself only updates
    # once the user clicks the link sent to `pending_email` (see
    # /u/<username>/change_email), so a user can't take over an inbox they don't
    # control — this also gates BMC webhook matching, which is keyed on `email`.
    pending_email = authDb.Column(authDb.String(100), nullable=True)
    email_verify_token = authDb.Column(authDb.String(100), nullable=True)

    def toDict(self):
        return {
            "uid": self.uid,
            "username": self.username,
            "email": self.email,
            "lang": self.lang,
            "leaderboard": self.leaderboard,
            "admin": self.admin,
            "alpha": self.alpha,
            "translator": self.translator,
            "creation_date": self.creation_date,
            "last_login": self.last_login,
            "reset_token": self.reset_token,
            "gps_token": self.gps_token,
            "mcp_token": self.mcp_token,
            "share_level": self.share_level,
            "user_currency": self.user_currency,
            "colorblind": self.colorblind,
            "tileserver": self.tileserver,
            "globe": self.globe,
            "premium": self.premium,
            "feature_admin": self.feature_admin,
            "flight_3d": self.flight_3d,
            "live_tracking": self.live_tracking,
            "discord_id": self.discord_id,
            "discord_username": self.discord_username,
            "pending_email": self.pending_email,
        }

    def is_public(self):
        return True if self.share_level >= 2 else False

    def is_public_trips(self):
        return True if self.share_level >= 1 else False


class PendingBmcEvent(authDb.Model):
    """A BMC membership webhook that couldn't be matched to a Trainlog user at
    the time it arrived (no User.email matches the supporter's BMC email). Kept
    so it can be replayed if a user later confirms a matching email change (see
    /u/<username>/change_email), clicks the claim link emailed to them (see
    /api/bmc/claim/<token>), or the owner manually assigns it (/api/bmc/assign)."""

    __tablename__ = "pending_bmc_event"
    id = authDb.Column(authDb.Integer, primary_key=True)
    supporter_email = authDb.Column(authDb.String(100), nullable=False, index=True)
    # BMC's stable per-supporter id, carried through so it can be pinned onto
    # the User once this event is applied (see User.bmc_supporter_id).
    supporter_id = authDb.Column(authDb.String(30), nullable=True)
    grant = authDb.Column(authDb.Boolean, nullable=False)
    tier = authDb.Column(authDb.String(20), nullable=True)
    event_type = authDb.Column(authDb.String(50), nullable=False)
    # Shared across every pending row for the same supporter_email, so one
    # emailed link claims all of them at once. Set the first time we email a
    # given address; reused (not regenerated) for later events from that email.
    claim_token = authDb.Column(authDb.String(64), nullable=True, index=True)
    created_at = authDb.Column(
        authDb.DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )


class Friendship(authDb.Model):
    __tablename__ = "friendship"
    id = authDb.Column(authDb.Integer, primary_key=True)
    user_id = authDb.Column(
        authDb.Integer, authDb.ForeignKey("user.uid"), nullable=False
    )
    friend_id = authDb.Column(
        authDb.Integer, authDb.ForeignKey("user.uid"), nullable=False
    )
    created_at = authDb.Column(
        authDb.DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    accepted = authDb.Column(authDb.DateTime, default=None)

    # Relationships
    user = authDb.relationship("User", foreign_keys=[user_id], backref="user_friends")
    friend = authDb.relationship(
        "User", foreign_keys=[friend_id], backref="friend_users"
    )
