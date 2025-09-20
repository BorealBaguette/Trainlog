import secrets

from flask import Flask
from flaskext.autoversion import Autoversion
from flask_caching import Cache
from flask_compress import Compress
from datetime import timedelta


class FlaskApp():
    def __init__(self, __name__, sql_db_uri: str, secret_file_path: str):
        # Passing in the name so that templates can be loaded from the right
        # location
        # TODO: Clean up __nam__ so it doesn't need to passed in
        self.app = Flask(__name__)
        self._enable_debug()
        self._compress_app()
        self._enable_autoversion()
        self.app.url_map.strict_slashes = False
        self._setup_secret_key(secret_file_path)
        self._setup_sql_alchemy(sql_db_uri)

    def register_blueprint(self, *args, **kwargs):
        self.app.register_blueprint(*args, **kwargs)

    def a(self):
        return __name__

    def _enable_debug(self):
        self.app.config['DEBUG'] = True

    def _compress_app(self):
        Compress(self.app)

    def _enable_autoversion(self):
        self.app.autoversion = True
        Autoversion(self.app)

    def _enable_cache(self):
        self.app.config["CACHE_TYPE"] = "SimpleCache"
        self.app.config["CACHE_DEFAULT_TIMEOUT"] = 864000
        self.cache = Cache(self.app)

    def _setup_sql_alchemy(self, sql_db_uri: str):
        self.app.config["SQLALCHEMY_DATABASE_URI"] = sql_db_uri
        self.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # SECRET_KEY required for session, flash and Flask Sqlalchemy to work
    def _setup_secret_key(self, secret_file_path: str):
        try:
            with secret_file_path.open("r") as secret_file:
                self.app.secret_key = secret_file.read()
        except FileNotFoundError:
            # Let's create a cryptographically secure code in that file
            with secret_file_path.open("w") as secret_file:
                self.app.secret_key = secrets.token_hex(32)
                secret_file.write(self.app.secret_key)

        self.app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

    def get_raw_app(self) -> Flask:
        return self.app

    def init_db(self, db):
        db.init_app(self.app)
