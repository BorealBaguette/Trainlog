"""Site-wide light/dark theme (beta).

The choice lives on the user row and is copied into session["userinfo"] with the
other settings, but no route passes it: a context processor injects `theme` into
every render_template call instead. That keeps the ~90 render_template sites
untouched, unlike `colorblind`, which is threaded through each route by hand.
"""

from flask import session
from sqlalchemy import text

from src.operators import OperatorsRepository
from src.users import authDb

THEMES = ("auto", "light", "dark")

DEFAULT_THEME = "light"


def ensure_theme_columns():
    """Add user.theme and user.dark_leaflet to an existing auth.db.

    The auth database is SQLite driven straight off the SQLAlchemy model, with no
    migration runner of its own (the numbered migrations under src/sql are
    Postgres-only), so a new column has to be added here.
    """
    with authDb.engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(user)"))}
        if "theme" not in columns:
            conn.execute(
                text(f"ALTER TABLE user ADD COLUMN theme VARCHAR(10) "
                     f"NOT NULL DEFAULT '{DEFAULT_THEME}'")
            )
        if "dark_leaflet" not in columns:
            conn.execute(text("ALTER TABLE user ADD COLUMN dark_leaflet BOOLEAN NOT NULL DEFAULT 0"))


def fill_session(userinfo, user):
    userinfo["theme"] = user.theme if user else DEFAULT_THEME
    userinfo["dark_leaflet"] = user.dark_leaflet if user else False


def save_settings(user, form, userinfo):
    """The settings page's theme fields onto the user and the session."""
    if form.get("theme") in THEMES:
        user.theme = form["theme"]
    user.dark_leaflet = "dark_leaflet" in form
    fill_session(userinfo, user)


def init_app(app):
    with app.app_context():
        ensure_theme_columns()

    @app.context_processor
    def inject_theme():
        userinfo = session.get("userinfo", {})
        # The admin-made dark logos. Called from theme_head only, and only when the
        # page may turn out dark (?theme= can override the setting), so other renders
        # (partials, emails) and light pages skip the query.
        return {
            "theme": userinfo.get("theme", DEFAULT_THEME),
            "dark_leaflet": userinfo.get("dark_leaflet", False),
            "dark_logos": OperatorsRepository.get_dark_logos,
        }
