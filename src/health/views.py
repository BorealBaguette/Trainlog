"""/admin/health: owner-only live view of the gunicorn workers, latency, host
and Postgres, with history. Registered both in the site and in the
trainlog_health sidecar (src/health/standalone.py), which Traefik routes this
path to first, so the page loads when every site worker is busy.

Nothing here imports the app: the owner check reads the same signed session
cookie the site sets, against the owner name in config.yaml.
"""

import re
from functools import wraps

import yaml
from flask import Blueprint, abort, jsonify, render_template, request, session

from src.health import collect
from src.health.recorder import ROUTE_ENVIRON_KEY

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


# Route arguments whose value names a kind of thing, never a person or a record,
# and has few values: kept in the recorded route so /forwardRouting/bus/... and
# /forwardRouting/train/... are timed apart. Everything else stays a <placeholder>.
KEEP_ARGS = frozenset({
    "routingType", "routing", "tripType", "trip_type", "vehicle_type", "type",
    "station_type", "mode", "kind", "metric", "period", "time", "style", "ext",
})
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


def route_label(rule, view_args):
    pattern = rule.rule
    for name in KEEP_ARGS.intersection(view_args or ()):
        value = str(view_args[name])
        if _SAFE_VALUE.match(value):
            pattern = re.sub(r"<(?:[^<>]*:)?%s>" % re.escape(name), value, pattern)
    return pattern


def record_route(app):
    """Hand each request's route pattern (/u/<username>/trips, not the path) to
    the gunicorn recorder, which reads it in post_request. teardown_request runs
    for every request, failed ones included, after the URL has been matched."""

    @app.teardown_request
    def _health_record_route(exc=None):
        rule = request.url_rule
        request.environ[ROUTE_ENVIRON_KEY] = (
            route_label(rule, request.view_args) if rule is not None else None
        )


@health_blueprint.route("/routes")
@owner_only
def health_routes():
    resp = jsonify(collect.routes(request.args.get("range", "1h"), request.args.get("compare", "prev")))
    resp.headers["Cache-Control"] = "no-store"
    return resp


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


@health_blueprint.route("/route")
@owner_only
def health_route_page():
    return render_template("admin/health_route.html", ranges=list(collect.RANGES), key=request.args.get("r", ""))


@health_blueprint.route("/route/data")
@owner_only
def health_route_data():
    resp = jsonify(collect.route_series(
        request.args.get("range", "24h"), request.args.get("r", ""),
        max_points=request.args.get("points", 300, type=int),
        frm=request.args.get("from", type=float), to=request.args.get("to", type=float),
    ))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@health_blueprint.route("/charts")
@owner_only
def health_charts():
    return render_template("admin/health_charts.html", ranges=list(collect.RANGES))


@health_blueprint.route("/history")
@owner_only
def health_history():
    points = request.args.get("points", collect.MAX_POINTS, type=int)
    resp = jsonify(collect.history(
        request.args.get("range", "1h"), max_points=points,
        frm=request.args.get("from", type=float), to=request.args.get("to", type=float),
    ))
    resp.headers["Cache-Control"] = "no-store"
    return resp
