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


def ensure_theme_column():
    """Add user.theme to an existing auth.db.

    The auth database is SQLite driven straight off the SQLAlchemy model, with no
    migration runner of its own (the numbered migrations under src/sql are
    Postgres-only), so a new column has to be added here.
    """
    with authDb.engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(user)"))}
        if "theme" not in columns:
            conn.execute(
                text(f"ALTER TABLE user ADD COLUMN theme VARCHAR(10) "
                     f"NOT NULL DEFAULT '{DEFAULT_THEME}'")
            )
            conn.commit()


def init_app(app):
    with app.app_context():
        ensure_theme_column()

    @app.context_processor
    def inject_theme():
        theme = session.get("userinfo", {}).get("theme", DEFAULT_THEME)
        # The admin-made dark logos. Called from theme_head only, and only when the
        # page may turn out dark (?theme= can override the setting), so other renders
        # (partials, emails) and light pages skip the query.
        return {"theme": theme, "dark_logos": OperatorsRepository.get_dark_logos}
