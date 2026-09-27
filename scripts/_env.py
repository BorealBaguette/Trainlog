"""Load .env for scripts run by hand, which the Makefile would otherwise provide.

POSTGRES_HOST is the Docker service name, which does not resolve from the host machine;
there the published port on localhost is used instead. Parsed by hand because
python-dotenv is only a transitive dependency.
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
    """Populate os.environ from .env without overriding what is set. Returns POSTGRES_HOST."""
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
