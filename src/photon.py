import json
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

# Autocomplete fires on every keystroke, and asking for two languages doubled the request
# count, so identical queries are held briefly in process.
#
# Sized from measurement: a 15-result response is ~6KB, so 1024 entries is ~6MB per gunicorn
# worker and ~24MB across the four. The TTL is short because Photon's index is refreshed from
# a dump and a stale answer only ever costs one repeated query.
#
# Deliberately per-process rather than in the shared Flask cache: the value is absorbing the
# burst of prefixes one user types ("p", "pa", "par", "pari"), which lands in whichever worker
# took the request, and doing that with no serialisation cost and no external dependency.
#
# The *text* is cached rather than the parsed object, so every caller gets its own structure.
# The station pipeline renames features in place, and handing two callers the same dict made
# the second one re-prefix an already-prefixed name into "Paris - Paris - Gare de Lyon".
_CACHE_MAX_ENTRIES = 1024
_CACHE_TTL_SECONDS = 300

_cache = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(instance, endpoint, params):
    # Params carry lists (osm_tag is repeated) and the values arrive as both str and int
    # depending on the caller, so sort_keys + default=str gives one stable key per query.
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

# The self-hosted instance is the only one available.
#
# There used to be a fallback chain of three (photon.chiel.uk, photon.komoot.io), tried in
# order until one answered. It stopped being a fallback: chiel is gone and komoot banned this
# project for excessive usage, so every request paid two guaranteed failures before reaching
# the instance that was always going to serve it. Worse, on the rare occasion a fallback did
# fire it answered from different data of a different vintage, so osm_ids and result sets did
# not line up with the primary — which is precisely what broke the earlier attempt at deriving
# international names by cross-referencing two instances.
#
# Kept as a dict because /photon_status/<instance> and the status page iterate over it.
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

    Photon answers in one language at a time, but the international name needs both the local
    name (`lang=default`) and the English one (`lang=en`) for the same places. Two requests
    against one instance return the same features in the same order, so the results join 1:1
    on (osm_type, osm_id) — measured at 100% overlap across queries in DE/RU/JP/UA/GR/KR/CH/BE.

    That join is only sound because there is a single instance now (see photonInstances):
    against the old fallback chain the two passes could land on different servers holding
    different data, which is what sank the earlier attempt at this.

    Run in parallel, the second language costs ~5ms on top of a ~56ms query. A language that
    fails comes back as None rather than failing the whole call, so the caller can degrade to
    whichever pass did answer.
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
