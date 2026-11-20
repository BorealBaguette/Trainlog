"""Cheaper static files while the gunicorn workers still serve them.

Nothing sits in front of Flask for /static/, and the health page showed static
files taking ~40% of all worker time (Nov 2026): every request recompressed
the file (Flask-Compress, no cache) and browsers re-requested every file on
every page load (no max-age). So:

- The compressed body of a static file is cached per worker, keyed by its path,
  mtime and size, so a changed file is recompressed even without a restart.
  Flask-Compress applies its cache to every response it compresses, dynamic
  pages included; StaticOnlyCache stores static files and ignores the rest.
- Browsers keep a static file for STATIC_MAX_AGE, or VERSIONED_MAX_AGE when the
  URL is versioned (static_autoversion adds ?ts=<mtime>). Unversioned URLs
  (url_for('static', ...), hard-coded /static/...) can serve a stale file for
  up to STATIC_MAX_AGE after a deploy. Other send_file routes keep Flask's
  default (no max-age).

Must run before Compress(app), which reads its cache settings when it starts.
"""

import os
import threading

from flask import current_app, request
from werkzeug.security import safe_join

STATIC_MAX_AGE = 3600
VERSIONED_MAX_AGE = 365 * 86400
CACHE_MAX_BYTES = 64 * 2**20  # per worker

_PREFIX = "static:"
_SEP = "\0"


class StaticOnlyCache:
    """Flask-Compress cache backend (get/set) that keeps one compressed copy per
    file and algorithm, replacing it when the file changes, within max_bytes."""

    def __init__(self, max_bytes=CACHE_MAX_BYTES):
        self.max_bytes = max_bytes
        self._entries = {}  # "algo;static:path" -> (full key, body)
        self._bytes = 0
        self._lock = threading.Lock()

    @staticmethod
    def _base(key):
        # Flask-Compress builds keys as f"{algorithm};{COMPRESS_CACHE_KEY(request)}".
        algo, _, rest = key.partition(";")
        if not rest.startswith(_PREFIX) or _SEP not in rest:
            return None
        return algo + ";" + rest.split(_SEP, 1)[0]

    def get(self, key):
        base = self._base(key)
        if base is None:
            return None
        entry = self._entries.get(base)
        return entry[1] if entry and entry[0] == key else None

    def set(self, key, value):
        base = self._base(key)
        if base is None or not value:
            return
        with self._lock:
            old = self._entries.get(base)
            if old and old[0] == key:
                return
            freed = len(old[1]) if old else 0
            if self._bytes - freed + len(value) > self.max_bytes:
                return
            self._entries[base] = (key, value)
            self._bytes += len(value) - freed


def compress_cache_key(req):
    """Key for a static file's current version; "-" (never stored) otherwise."""
    if req.endpoint != "static" or "Range" in req.headers:
        return "-"
    filename = (req.view_args or {}).get("filename")
    path = safe_join(current_app.static_folder, filename) if filename else None
    if not path:
        return "-"
    try:
        st = os.stat(path)
    except OSError:
        return "-"
    return f"{_PREFIX}{filename}{_SEP}{st.st_mtime_ns}:{st.st_size}"


def init_static_caching(app):
    app.config["COMPRESS_CACHE_BACKEND"] = StaticOnlyCache
    app.config["COMPRESS_CACHE_KEY"] = compress_cache_key

    def get_send_file_max_age(filename):
        # Flask's static view and send_file both ask the app; only the static
        # view gets a lifetime, everything else keeps the default (None).
        if request and request.endpoint == "static":
            return VERSIONED_MAX_AGE if request.args.get("ts") else STATIC_MAX_AGE
        return None

    app.get_send_file_max_age = get_send_file_max_age
