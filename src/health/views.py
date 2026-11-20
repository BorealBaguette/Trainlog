"""/admin/health: owner-only live view of the gunicorn workers, latency, host
and Postgres, with history. Registered both in the site and in the
trainlog_health sidecar (src/health/standalone.py), which Traefik routes this
path to first, so the page loads when every site worker is busy.

Nothing here imports the app: the owner check reads the same signed session
cookie the site sets, against the owner name in config.yaml.
"""

from functools import wraps

import yaml
from flask import Blueprint, abort, jsonify, render_template, request, session

from src.health import collect

health_blueprint = Blueprint("health", __name__)

_owner = None


def _owner_name():
    global _owner
    if _owner is None:
        # yaml directly: py.utils.load_config's module loads the geodata, which
        # the sidecar has no use for.
        with open("config.yaml", encoding="utf-8") as f:
            _owner = yaml.safe_load(f)["owner"]["username"]
    return _owner


def owner_only(f):
    # Same test as src.utils.owner_required, which can't be imported here.
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get(_owner_name()):
            abort(401)
        return f(*args, **kwargs)

    return wrapper


@health_blueprint.route("/")
@owner_only
def health_page():
    return render_template("admin/health.html", ranges=list(collect.RANGES))


@health_blueprint.route("/live")
@owner_only
def health_live():
    resp = jsonify(collect.sample())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@health_blueprint.route("/history")
@owner_only
def health_history():
    resp = jsonify(collect.history(request.args.get("range", "1h")))
    resp.headers["Cache-Control"] = "no-store"
    return resp
