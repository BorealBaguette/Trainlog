import json
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

# Absorbs the burst of prefixes one user types. Per process, as each burst lands on one
# worker. The text is cached rather than the parsed object because the station pipeline
# modifies features in place.
_CACHE_MAX_ENTRIES = 1024
_CACHE_TTL_SECONDS = 300

_cache = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(instance, endpoint, params):
    return json.dumps(
        [instance, endpoint, params], sort_keys=True, default=str
    )


def _cache_get(key):
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < now:
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return value


def _cache_put(key, value):
    with _cache_lock:
        _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)

# A dict because /photon_status/<instance> and the status page iterate over it.
photonInstances = {
    "trainlog": "https://photon.srv.trainlog.me",
}

DEFAULT_INSTANCE = "trainlog"


def photonRequestSingle(instance, endpoint, params, *, timeout=5, use_cache=True):
    url = photonInstances[instance]
    endpoint = endpoint.lstrip("/")

    # /status reports the index's import date and must not be served from a cache.
    use_cache = use_cache and not endpoint.startswith("status")
    key = _cache_key(instance, endpoint, params) if use_cache else None
    if key is not None:
        cached = _cache_get(key)
        if cached is not None:
            return json.loads(cached)

    resp = requests.get(f"{url}/{endpoint}", params=params, timeout=timeout)
    resp.raise_for_status()

    if key is not None:
        _cache_put(key, resp.text)
    return resp.json()


def photonRequest(endpoint, params, *, timeout=5):
    """Query Photon. Returns None if it cannot be reached, as callers expect."""
    try:
        return photonRequestSingle(
            DEFAULT_INSTANCE, endpoint, params, timeout=timeout
        )
    except Exception as e:
        logger.warning(f"Photon request failed: {e}")
        return None


def photonRequestLangs(endpoint, params, langs, *, timeout=5):
    """Run the same query once per language, in parallel. Returns {lang: json | None}.

    The international name needs both the local and the English name of the same places; one
    instance returns the same features for both, so they join on (osm_type, osm_id). A failed
    language comes back as None.
    """
    langs = list(langs)

    def fetch(lang):
        try:
            return photonRequestSingle(
                DEFAULT_INSTANCE,
                endpoint,
                {**params, "lang": lang},
                timeout=timeout,
            )
        except Exception as e:
            logger.warning(f"Photon request failed (lang={lang}): {e}")
            return None

    with ThreadPoolExecutor(max_workers=len(langs)) as executor:
        return dict(zip(langs, executor.map(fetch, langs)))
