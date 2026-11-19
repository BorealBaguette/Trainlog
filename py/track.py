import ipaddress
import random
import re
import time
from urllib.parse import urlsplit

from flask import current_app, g, request
from werkzeug.exceptions import HTTPException, MethodNotAllowed
from werkzeug.routing import RequestRedirect

from py.flask_matomo import Matomo
from py.utils import getIp


def truncate_ip(ip):
    """Anonymise an IP before it reaches Matomo: IPv4 to /24 (last byte zeroed),
    IPv6 to /48. Anything unparseable is dropped rather than sent as-is."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    prefix = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False).network_address)


def external_referrer(referrer):
    """Only the origin of an outside referrer: an internal one is the previous page's
    full URL (username, tokens), and an outside one may carry its own query string."""
    if not referrer:
        return None
    parts = urlsplit(referrer)
    if not parts.scheme or not parts.netloc or parts.netloc == request.host:
        return None
    return f"{parts.scheme}://{parts.netloc}/"


# The mobile app's API client and WebViews put this token in their User-Agent.
APP_UA_TOKEN = "TrainlogApp/"

# JSON-RPC methods the MCP endpoint answers; anything else is reported as "other"
# so a client can't fill Matomo with made-up action names.
MCP_METHODS = {
    "initialize",
    "ping",
    "tools/list",
    "tools/call",
    "notifications/initialized",
    "notifications/cancelled",
}
MCP_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")

# Crawlers and link previews (Mastodon/Fediverse servers, chat apps, social networks,
# search engines). Some send a browser-like "Mozilla/5.0 (compatible; XBot)" string.
BOT_UA = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|"
    r"mastodon|pleroma|akkoma|misskey|friendica|lemmy|bluesky|cardyb|iframely|embedly",
    re.IGNORECASE,
)


def is_site_path(path, method="GET"):
    """Whether a path leads to a real page. Bare paths like /trips go through the
    catch-all user_shortcut, which redirects to /u/<user>/trips, so those count
    when the /u/ version exists."""
    path = urlsplit(path).path or "/"
    adapter = current_app.url_map.bind("localhost")
    for candidate in (path, "/u/_/" + path.lstrip("/")):
        try:
            endpoint, _ = adapter.match(candidate, method=method)
        except (RequestRedirect, MethodNotAllowed):
            return True
        except HTTPException:
            return False
        if endpoint != "user_shortcut":
            return True
    return False


def is_scan():
    """A vulnerability scanner probing paths the site never had (/wp-admin, /.env):
    the catch-all route, or the login page it sends anonymous visitors to with
    ?next=<that path>. Meaningless as traffic, so the only thing left untracked."""
    if request.url_rule is None:
        return True
    if request.endpoint == "user_shortcut":
        return not is_site_path(request.path, request.method)
    if request.endpoint == "login":
        next_path = request.args.get("next", "")
        return next_path.startswith("/") and not is_site_path(next_path)
    return False


def client_source(user_agent, is_mcp):
    """Who is asking, for the "source" visit dimension. Matomo's own device and
    browser detection can't name anything that isn't a browser; this can."""
    if is_mcp:
        return "mcp"
    if APP_UA_TOKEN in user_agent:
        return "app"
    if BOT_UA.search(user_agent):
        return "bot"
    if user_agent.startswith("Mozilla/"):
        return "web"
    # curl, python-requests, Go, Tasker, an empty User-Agent…
    return "other"


def mcp_actions():
    """What an MCP POST asks for, as Matomo URL paths: "tools/call/add_trip",
    "tools/list"… Only the method and tool name, never the arguments."""
    body = request.get_json(silent=True)
    messages = body if isinstance(body, list) else [body]
    actions = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        method = msg.get("method")
        if method == "tools/call":
            name = (msg.get("params") or {}).get("name")
            if not (isinstance(name, str) and MCP_TOOL_NAME.fullmatch(name)):
                name = "unknown_tool"
            actions.add(f"tools/call/{name}")
        else:
            actions.add(method if method in MCP_METHODS else "other")
    return sorted(actions) or ["invalid"]


class CustomMatomo(Matomo):
    """A custom Matomo class that overrides the before_request method to include the client's IP address."""

    # Id of a visit-scope custom dimension in Matomo that receives client_source()
    # (web, app, mcp, bot, other), so every visit is labelled. None = not sent.
    client_dimension_id = None

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
        if is_scan():
            return
        is_mcp = request.blueprint == "mcp"

        # The route pattern, not the real URL: /u/<username>/trips, never the username,
        # a query string or a path token (password reset, GPS, MCP, BMC claim).
        url = (self.base_url or request.host_url.rstrip("/")) + (
            url_rule if request.url_rule else "/[unmatched]"
        )
        # action_name = request.url_rule
        action_name = request.endpoint
        if is_mcp:
            # Every MCP message is a POST to /mcp: name the hit after the tool instead.
            # A batch (rare) is reported as its distinct actions joined with "+".
            actions = mcp_actions()
            url = url.rstrip("/") + "/" + "+".join(actions)
            action_name = "mcp: " + " + ".join(
                a.removeprefix("tools/call/") for a in actions
            )
        user_agent = request.user_agent
        ip_address = getIp(request)
        if ip_address in ("127.0.0.1"):
            return

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
            "cvar": {
                "http_status_code": None,
                "http_method": str(request.method),
            },
            "urlref": external_referrer(request.headers.get("Referer")),
            # random data
            "rand": random.getrandbits(32),
        }
        if self.token_auth:
            data["token_auth"] = self.token_auth
            cip = truncate_ip(ip_address)
            if cip:
                data["cip"] = cip

        if request.accept_languages:
            data["lang"] = request.accept_languages[0][0]

        if self.client_dimension_id:
            data[f"dimension{self.client_dimension_id}"] = client_source(
                str(user_agent), is_mcp
            )

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
