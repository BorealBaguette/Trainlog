"""Cross-site request protection for the session cookie.

SameSite=Lax keeps browsers from sending the cookie with another site's POSTs,
fetches and images. It counts every *.trainlog.me host as the same site though,
so writes that carry the cookie must also come from this very origin.
"""

from urllib.parse import urlparse

from flask import abort, request

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def init_csrf(app):
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True

    @app.before_request
    def check_origin():
        if request.method in SAFE_METHODS:
            return
        # Webhooks and token APIs carry no session to ride on, and non-browser
        # clients (the mobile app) send no Origin; browsers always do on POST.
        if app.config["SESSION_COOKIE_NAME"] not in request.cookies:
            return
        origin = request.headers.get("Origin")
        # "null" is what a WebView sends for a POST its app makes (the app opens
        # the routing page that way); another site's POST no longer carries the
        # cookie at all, SameSite sees to that.
        if origin not in (None, "null") and urlparse(origin).netloc != request.host:
            abort(403)
