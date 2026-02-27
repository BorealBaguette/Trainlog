import json
import logging
import random
import re
import time
import typing

import flask
import httpx
from flask import g, request

logger = logging.getLogger("flask_matomo2")


class Matomo:
    """The Matomo object provides the central interface for interacting with Matomo.

    Parameters
    ----------
    app : Flask
        created with Flask(__name__)
    matomo_url : str
        url to Matomo installation
    id_site : int
        id of the site that should be tracked on Matomo
    token_auth : str
        token that can be found in the area API in the settings of Matomo
    base_url : str
        url to the site that should be tracked
    ignored_patterns : str
        list of regexes to ignore. Default: None.
    """

    def __init__(
        self,
        app=None,
        *,
        matomo_url: str,
        id_site=None,
        token_auth=None,
        base_url=None,
        client=None,
        ignored_routes: typing.Optional[typing.List[str]] = None,
        routes_details: typing.Optional[typing.Dict[str, typing.Dict[str, str]]] = None,
        ignored_patterns: typing.Optional[typing.List[str]] = None,
        ignored_ua_patterns: typing.Optional[typing.List[str]] = None,
    ):
        self.activate(
            app=app,
            matomo_url=matomo_url,
            id_site=id_site,
            token_auth=token_auth,
            base_url=base_url,
            client=client,
            ignored_routes=ignored_routes,
            routes_details=routes_details,
            ignored_patterns=ignored_patterns,
            ignored_ua_patterns=ignored_ua_patterns,
        )

    @classmethod
    def activate_later(cls) -> "Matomo":
        return cls(matomo_url="NOT SET")

    def activate(
        self,
        app=None,
        *,
        matomo_url: str,
        id_site=None,
        token_auth=None,
        base_url=None,
        client=None,
        ignored_routes: typing.Optional[typing.List[str]] = None,
        routes_details: typing.Optional[typing.Dict[str, typing.Dict[str, str]]] = None,
        ignored_patterns: typing.Optional[typing.List[str]] = None,
        ignored_ua_patterns: typing.Optional[typing.List[str]] = None,
    ):
        # Silence httpx request logging
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self.app = app
        self.matomo_url = matomo_url
        self.id_site = id_site
        self.token_auth = token_auth
        self.base_url = base_url.strip("/") if base_url else base_url
        self.ignored_ua_patterns = []
        if ignored_ua_patterns:
            self.ignored_ua_patterns = [
                re.compile(pattern) for pattern in ignored_ua_patterns
            ]
        self.ignored_routes: typing.List[str] = ignored_routes or []
        self.routes_details: typing.Dict[str, typing.Dict[str, str]] = (
            routes_details or {}
        )
        self.client = client or httpx.Client()
        self.ignored_patterns = []
        if ignored_patterns:
            self.ignored_patterns = [
                re.compile(pattern) for pattern in ignored_patterns
            ]

        if not matomo_url:
            raise ValueError("matomo_url has to be set")
        if not self.token_auth:
            logger.warning("'token_auth' not given, NOT tracking ip-address")

        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """Initialize app"""
        app.before_request(self.before_request)
        app.after_request(self.after_request)
        app.teardown_request(self.teardown_request)

    def before_request(self):
        """Executed before every request, parses details about request"""
        # Don't track track request, if user used ignore() decorator for route
        url_rule = str(request.url_rule)
        if url_rule in self.ignored_routes:
            return
        if any(
            ua_pattern.match(str(request.user_agent))
            for ua_pattern in self.ignored_ua_patterns
        ):
            return
        if any(pattern.match(url_rule) for pattern in self.ignored_patterns):
            return

        url = self.base_url + request.path if self.base_url else request.url
        action_name = url_rule if request.url_rule else "Not Found"
        user_agent = request.user_agent
        # If request was forwarded (e.g. by a proxy), then get origin IP from
        # HTTP_X_FORWARDED_FOR. If this header field doesn't exist, return
        # remote_addr.
        ip_address = request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr)

        data = {
            # site data
            "idsite": str(self.id_site),
            "rec": "1",
            "apiv": "1",
            "send_image": "0",
            # request data
            "ua": user_agent,
            "action_name": action_name,
            "url": url,
            # "_id": id,
            "cvar": {
                "http_status_code": None,
                "http_method": str(request.method),
            },
            # random data
            "rand": random.getrandbits(32),
        }
        if self.token_auth:
            data["token_auth"] = self.token_auth
            data["cip"] = ip_address

        if request.accept_languages:
            data["lang"] = request.accept_languages[0][0]

        # Overwrite action_name, if it was configured with details()
        if self.routes_details.get(action_name) and self.routes_details.get(
            action_name, {}
        ).get("action_name"):
            data["action_name"] = self.routes_details.get(action_name, {}).get(
                "action_name"
            )

        g.flask_matomo2 = {
            "tracking": True,
            "start_ns": time.perf_counter_ns(),
            "tracking_data": data,
        }

    def after_request(self, response: flask.Response):
        """Collect tracking data about current request."""
        tracking_state = g.get("flask_matomo2", {})
        if not tracking_state.get("tracking", False):
            return response

        end_ns = time.perf_counter_ns()
        gt_ms = (end_ns - g.flask_matomo2["start_ns"]) / 1000
        g.flask_matomo2["tracking_data"]["gt_ms"] = gt_ms
        g.flask_matomo2["tracking_data"]["cvar"]["http_status_code"] = (
            response.status_code
        )

        return response

    def teardown_request(self, exc: typing.Optional[Exception] = None) -> None:
        tracking_state = g.get("flask_matomo2", {})
        if not tracking_state.get("tracking", False):
            return
        logger.debug(f"{tracking_state=}")
        tracking_data = tracking_state["tracking_data"]
        for key, value in tracking_state.get("custom_tracking_data", {}).items():
            if key == "cvar" and "cvar" in tracking_data:
                tracking_data["cvar"].update(value)
            else:
                tracking_data[key] = value

        self.track(tracking_data=tracking_data)

    def track(
        self,
        *,
        tracking_data: typing.Dict,
    ):
        """Send request to Matomo

        Parameters
        ----------
        action_name : str
            name of the site
        url : str
            url to track
        user_agent : str
            User-Agent of request
        id : str
            id of user
        ip_address : str
            ip address of request
        lang : Optional[str]
            The client's preferred language, defaults to None.
        """
        if "cvar" in tracking_data:
            cvar = tracking_data.pop("cvar")
            tracking_data["cvar"] = json.dumps(cvar)
        logger.debug("calling '%s' with '%s'", self.matomo_url, tracking_data)
        try:
            r = self.client.post(self.matomo_url, data=tracking_data)

            if r.status_code >= 300:
                logger.error(
                    "Tracking call failed (status_code=%d)",
                    r.status_code,
                    extra={"status_code": r.status_code, "text": r.text},
                )
                # raise MatomoError(r.text)
        except httpx.HTTPError as exc:
            logger.exception("Tracking call failed:", extra={"exc": exc})
            logger.exception(exc)

    def ignore(self, route: typing.Optional[str] = None):
        """Ignore a route and don't track it.

        Parameters
        ----------
        route: str
            name of the route.

        Examples:
            @app.route("/admin")
            @matomo.ignore()
            def admin():
                return render_template("admin.html")
        """

        def wrap(f):
            route_name = route or self.guess_route_name(f.__name__)
            self.ignored_routes.append(route_name)
            return f

        return wrap

    def guess_route_name(self, path: str) -> str:
        return f"/{path}"

    def details(
        self,
        route: typing.Optional[str] = None,
        *,
        action_name: typing.Optional[str] = None,
    ):
        """Set details like action_name for a route

        Parameters
        ----------
        route: str
            name of the route.
        action_name : str
            name of the site

        Examples:
            @app.route("/users")
            @matomo.details(action_name="Users")
            def all_users():
                return jsonify(users=[...])
        """

        def wrap(f):
            route_details = {}
            if action_name:
                route_details["action_name"] = action_name

            if route_details:
                route_name = route or self.guess_route_name(f.__name__)
                self.routes_details[route_name] = route_details
            return f

        return wrap
