"""Load the environment a script needs to reach the database.

The app is started through the Makefile, which does `env $(cat .env | xargs)` and overrides
POSTGRES_HOST to localhost. A script run by hand gets neither, so `src.pg` raised a bare
KeyError on POSTGRES_HOST — which says nothing about what to do.

This reads .env the same way and picks a host that actually resolves from here:

  * On the server, POSTGRES_HOST is `trainlog_db`, the Docker service name, and works.
  * From a shell on the host machine that name does not resolve, but the port is published,
    so localhost is what is meant.

.env is parsed rather than loaded with python-dotenv on purpose: dotenv is not in
requirements.txt, it is only present as a transitive dependency, and a maintenance script
should not stop working the day that changes.
"""

import os
import socket


def _parse_env_file(path):
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                # Values may be quoted; the shell would strip those too.
                values[key.strip()] = value.strip().strip("'\"")
    except FileNotFoundError:
        pass
    return values


def _resolves(host):
    try:
        socket.gethostbyname(host)
        return True
    except OSError:
        return False


def load_env(repo_root=None):
    """Populate os.environ from .env, without overriding what is already set.

    Returns the host chosen for POSTGRES_HOST, so a caller can report it.
    """
    repo_root = repo_root or os.path.join(os.path.dirname(__file__), "..")
    for key, value in _parse_env_file(os.path.join(repo_root, ".env")).items():
        os.environ.setdefault(key, value)

    host = os.environ.get("POSTGRES_HOST")
    # An explicit override always wins; only the value read from .env is second-guessed.
    if host and not _resolves(host):
        os.environ["POSTGRES_HOST"] = "localhost"
        host = "localhost"
    elif not host:
        os.environ["POSTGRES_HOST"] = "localhost"
        host = "localhost"

    missing = [
        key
        for key in ("POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
        if not os.environ.get(key)
    ]
    if missing:
        raise SystemExit(
            f"Missing database settings: {', '.join(missing)}.\n"
            f"Expected them in {os.path.abspath(os.path.join(repo_root, '.env'))} "
            f"or in the environment."
        )
    return host
