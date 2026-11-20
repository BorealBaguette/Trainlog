"""The trainlog_health sidecar: a Flask app serving /admin/health and nothing
else, run by its own one-worker gunicorn (see trainlog_infra). It shares the
site's code directory, secret key and session cookie, so the owner's normal
login works, and reads the worker files through the shared status volume.

    gunicorn -b 0.0.0.0:5000 src.health.standalone:app --workers=1 --threads=4
"""

import pathlib
from datetime import timedelta

from flask import Flask

from src.health import collect
from src.health.views import health_blueprint

ROOT = pathlib.Path(__file__).resolve().parents[2]

app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=None)
# Same key file and lifetime as app.py, so the site's session cookie verifies
# here and a refreshed one stays valid there.
app.secret_key = (ROOT / ".flask_secret").read_text()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.url_map.strict_slashes = False
app.register_blueprint(health_blueprint, url_prefix="/admin/health")

collect.start_sampler()
