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
        authDb.DateTime, nullable=False, default=datetime.now(UTC)
    )
    last_login = authDb.Column(
        authDb.DateTime, nullable=False, default=datetime.now(UTC)
    )
    admin = authDb.Column(authDb.Boolean, nullable=False, default=False)
    alpha = authDb.Column(authDb.Boolean, nullable=False, default=False)
    translator = authDb.Column(authDb.Boolean, nullable=False, default=False)
    user_currency = authDb.Column(authDb.String(3), nullable=False, default="EUR")
    friend_search = authDb.Column(authDb.Boolean, nullable=False, default=True)
    reset_token = authDb.Column(authDb.String(100), default="")
    default_landing = authDb.Column(authDb.String(20), nullable=False, default="map")
    appear_on_global = authDb.Column(authDb.Boolean, nullable=False, default=False)
    tileserver = authDb.Column(authDb.String(50), nullable=False, default="default")
    globe = authDb.Column(authDb.Boolean, nullable=False, default=False)
    premium = authDb.Column(authDb.Boolean, nullable=False, default=False)

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
            "share_level": self.share_level,
            "user_currency": self.user_currency,
            "tileserver": self.tileserver,
            "globe": self.globe,
            "premium": self.premium,
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
        authDb.DateTime, nullable=False, default=datetime.now(UTC)
    )
    accepted = authDb.Column(authDb.DateTime, default=None)

    # Relationships
    user = authDb.relationship("User", foreign_keys=[user_id], backref="user_friends")
    friend = authDb.relationship(
        "User", foreign_keys=[friend_id], backref="friend_users"
    )
