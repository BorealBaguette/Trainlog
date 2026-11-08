import ipaddress
import random
import time
from urllib.parse import urlsplit

from flask import g, request

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


class CustomMatomo(Matomo):
    """A custom Matomo class that overrides the before_request method to include the client's IP address."""

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

        # The route pattern, not the real URL: /u/<username>/trips, never the username,
        # a query string or a path token (password reset, GPS, MCP, BMC claim).
        url = (self.base_url or request.host_url.rstrip("/")) + (
            url_rule if request.url_rule else "/[unmatched]"
        )
        # action_name = request.url_rule
        action_name = request.endpoint
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
