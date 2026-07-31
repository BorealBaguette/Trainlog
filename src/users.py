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
    feature_admin = authDb.Column(authDb.Boolean, nullable=False, default=False)
    # Premium-only: render flight tracks as a 3D altitude profile on trip pages.
    flight_3d = authDb.Column(authDb.Boolean, nullable=False, default=False)
    # Premium-only: while a flight is in the air, draw its flown-so-far track from FR24
    # instead of a geodesic. Off by default even for premium, because broadcasting a
    # real-time position is a materially different disclosure from a historical log.
    live_tracking = authDb.Column(authDb.Boolean, nullable=False, default=False)

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
        }

    def is_public(self):
        return True if self.share_level >= 2 else False

    def is_public_trips(self):
        return True if self.share_level >= 1 else False


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
