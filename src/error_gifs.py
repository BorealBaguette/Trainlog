"""GIFs shown on the error pages, and their curation.

Two sources feed the same pool, per HTTP status code:

  * base_data/error_gifs.json - curated GIPHY entries, kept as ids and served
    from GIPHY's own CDN, so we display their content under their terms rather
    than rehosting it;
  * static/images/errors/gifs/<code>/ - files we are free to host ourselves
    (own footage, CC0/CC-BY), with optional `.credit` and `.caption` sidecars.

Reads are cached until the manifest or a bucket directory changes, so adding a
GIF takes effect without a restart. The GIPHY API is only ever called from the
admin page - never while rendering an error page, which would put a network
call on the path of a page that is already the result of something going wrong.
"""

import json
import logging
import os
import random
import tempfile

import requests

from py.utils import load_config

logger = logging.getLogger(__name__)

MANIFEST = os.path.join("base_data", "error_gifs.json")
GIF_ROOT = os.path.join("static", "images", "errors", "gifs")
IMAGE_EXTS = (".gif", ".png", ".jpg", ".jpeg", ".webp", ".avif")
# Codes the error handler renders a page for; `default` covers the rest.
BUCKETS = ("401", "404", "410", "416", "500", "default")

GIPHY_API = "https://api.giphy.com/v1/gifs"
# giphy.gif is the one rendition guaranteed to exist for every id; the
# downsized variants are not always generated.
GIPHY_MEDIA = "https://media.giphy.com/media/{id}/giphy.gif"

# path -> (mtime, parsed content), for the manifest and the bucket listings
_cache: dict[str, tuple[float, object]] = {}


class GiphyError(Exception):
    """Raised when the GIPHY API cannot be reached or is not configured."""


def _cached_by_mtime(path, loader):
    """Return loader(path), recomputed only when path's mtime changes."""
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return None

    cached = _cache.get(path)
    if cached is None or cached[0] != mtime:
        try:
            cached = (mtime, loader(path))
        except (OSError, ValueError):
            return None
        _cache[path] = cached
    return cached[1]


def _read_sidecar(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def _list_bucket(directory):
    """Local image files in a bucket, as candidate dicts."""
    candidates = []
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(IMAGE_EXTS):
            continue
        if not os.path.isfile(os.path.join(directory, name)):
            continue

        stem = os.path.splitext(name)[0]
        # Where the file came from: first line the credit, second an optional
        # URL to link it to. `.caption` holds a caption we wrote ourselves.
        credit = _read_sidecar(os.path.join(directory, stem + ".credit"))
        caption = _read_sidecar(os.path.join(directory, stem + ".caption"))
        candidates.append(
            {
                "provider": "local",
                "ref": name,
                "path": f"images/errors/gifs/{os.path.basename(directory)}/{name}",
                "caption": caption[0] if caption else None,
                "credit": credit[0] if credit else None,
                "credit_url": credit[1] if len(credit) > 1 else None,
            }
        )
    return candidates


def _load_manifest_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _pick_caption(caption, lang_code):
    """Captions are either one string, or a {lang: string} map.

    Nothing here goes through lang/, so a plain string shows to everyone as
    written; the map is for the odd caption worth translating, falling back to
    English and then to whatever is there.
    """
    if not isinstance(caption, dict):
        return caption or None
    for key in (lang_code, "en"):
        if caption.get(key):
            return caption[key]
    return next((value for value in caption.values() if value), None)


def _bucket_candidates(bucket, manifest, lang_code):
    """Every GIF available for one bucket, GIPHY entries first."""
    candidates = [
        {
            "provider": "giphy",
            "ref": entry["id"],
            "src": GIPHY_MEDIA.format(id=entry["id"]),
            "caption": _pick_caption(entry.get("caption"), lang_code),
            "captions": entry.get("caption"),
            "credit": entry.get("title"),
            "credit_url": entry.get("url") or f"https://giphy.com/gifs/{entry['id']}",
        }
        for entry in manifest.get(bucket, [])
        if isinstance(entry, dict) and entry.get("id")
    ]
    candidates += _cached_by_mtime(os.path.join(GIF_ROOT, bucket), _list_bucket) or []
    return candidates


def random_error_gif(error_code, lang_code="en"):
    """Pick a random GIF for this error code, or None when there is none.

    Falls back to the `default` bucket when the code has nothing of its own;
    with both empty the error page simply shows no image.
    """
    manifest = _cached_by_mtime(MANIFEST, _load_manifest_file) or {}
    for bucket in (str(error_code), "default"):
        candidates = _bucket_candidates(bucket, manifest, lang_code)
        if candidates:
            return random.choice(candidates)
    return None


def error_gif_buckets(lang_code="en"):
    """All buckets and their GIFs, for the admin page. `default` comes last."""
    manifest = _cached_by_mtime(MANIFEST, _load_manifest_file) or {}
    codes = {key for key in manifest if key.isdigit()}
    try:
        codes |= {name for name in os.listdir(GIF_ROOT) if name.isdigit()}
    except OSError:
        pass

    return [
        {"code": code, "gifs": _bucket_candidates(code, manifest, lang_code)}
        for code in sorted(codes) + ["default"]
    ]


# --- curation, from the admin page only -------------------------------------


def _load_manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(manifest):
    """Write the manifest atomically, so a failed write cannot truncate it."""
    directory = os.path.dirname(MANIFEST) or "."
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, delete=False
    ) as tmp:
        json.dump(manifest, tmp, indent=4, ensure_ascii=False)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp.name, MANIFEST)


def _giphy_key():
    key = (load_config().get("giphy") or {}).get("key")
    if not key:
        raise GiphyError("No giphy.key in config.yaml")
    return key


def _giphy_get(endpoint, params):
    try:
        response = requests.get(
            f"{GIPHY_API}{endpoint}", params={"api_key": _giphy_key(), **params}, timeout=20
        )
        response.raise_for_status()
        return response.json()["data"]
    except requests.RequestException as e:
        logger.warning("GIPHY request failed: %s", e)
        raise GiphyError(str(e)) from e


def giphy_search(terms, limit=24):
    """Search GIPHY, returning what the admin page needs to show a result."""
    results = _giphy_get("/search", {"q": terms, "rating": "g", "limit": limit})
    return [
        {
            "id": gif["id"],
            "title": (gif.get("title") or "").strip(),
            "url": gif.get("url"),
            "src": GIPHY_MEDIA.format(id=gif["id"]),
            # Studios and networks upload to their own channels precisely so
            # their clips get embedded, which makes those the safest picks.
            "channel": (gif.get("user") or {}).get("display_name")
            or (gif.get("user") or {}).get("username"),
            "verified": bool((gif.get("user") or {}).get("is_verified")),
        }
        for gif in results
    ]


def add_gif(code, gif_id):
    """Add one GIPHY id to a bucket, looking its metadata up on GIPHY."""
    manifest = _load_manifest()
    bucket = manifest.setdefault(code, [])
    if any(entry.get("id") == gif_id for entry in bucket):
        raise ValueError(f"{gif_id} is already in bucket {code}")

    found = _giphy_get("", {"ids": gif_id})
    if not found:
        raise ValueError(f"{gif_id} was not found on GIPHY")

    gif = found[0]
    entry = {
        "id": gif["id"],
        "title": (gif.get("title") or "").strip() or None,
        "url": gif.get("url"),
    }
    bucket.append(entry)
    _save_manifest(manifest)
    return entry


def remove_gif(code, gif_id):
    manifest = _load_manifest()
    bucket = manifest.get(code, [])
    kept = [entry for entry in bucket if entry.get("id") != gif_id]
    if len(kept) == len(bucket):
        raise ValueError(f"{gif_id} is not in bucket {code}")
    manifest[code] = kept
    _save_manifest(manifest)


def set_caption(code, gif_id, text, lang_code=None):
    """Set our own caption, for every language or just one.

    An empty text clears it: the whole caption without a language, or that one
    language's entry when given.
    """
    manifest = _load_manifest()
    for entry in manifest.get(code, []):
        if entry.get("id") != gif_id:
            continue

        if not lang_code:
            if text:
                entry["caption"] = text
            else:
                entry.pop("caption", None)
        else:
            existing = entry.get("caption")
            if not isinstance(existing, dict):
                existing = {"en": existing} if existing else {}
            if text:
                existing[lang_code] = text
            else:
                existing.pop(lang_code, None)
            if existing:
                entry["caption"] = existing
            else:
                entry.pop("caption", None)

        _save_manifest(manifest)
        return entry.get("caption")
    raise ValueError(f"{gif_id} is not in bucket {code}")
