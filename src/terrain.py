"""Ground heights from Mapterhorn, the terrain the 3D trip view draws: Copernicus 30 m
worldwide with national lidar models on top (Norway, Switzerland and much of Europe at
about 1 m), as Terrarium-encoded 512 px WebP tiles. Read here when a trip is saved,
so its stored heights are bare ground on the same data the view shows, rather than the
router's surface model (tree tops and roofs, several metres up)."""

import io
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor

import requests
from PIL import Image

logger = logging.getLogger(__name__)

TILES_URL = "https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"
TILE = 512
MAX_ZOOM = 14  # a few metres a pixel in Europe: finer than any track's curvature
# A long route reads a coarser zoom, so a save fetches at most about this many tiles
MAX_TILES = 40
CACHE_DIR = "databases/cache/terrain"
HEADERS = {"User-Agent": "Trainlog (+https://trainlog.me)"}


class Unreachable(Exception):
    """Mapterhorn could not be reached (as opposed to a tile it does not have)."""


# Tiles read, and those Mapterhorn does not have; a failed request is not remembered, so a
# short outage does not leave a worker without terrain until it restarts
_tiles = {}
_MISSING = object()


def _tile(z, x, y):
    """The tile's RGB image, from memory, the disk cache or Mapterhorn; None if Mapterhorn
    has no such tile. Raises Unreachable when it could not be asked."""
    key = (z, x, y)
    if key in _tiles:
        return None if _tiles[key] is _MISSING else _tiles[key]
    path = os.path.join(CACHE_DIR, str(z), str(x), f"{y}.webp")
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
        else:
            response = requests.get(TILES_URL.format(z=z, x=x, y=y), headers=HEADERS, timeout=5)
            if response.status_code == 404:
                _tiles[key] = _MISSING
                return None
            response.raise_for_status()
            data = response.content
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except (requests.RequestException, OSError) as e:
        logger.warning("terrain tile %s/%s/%s unavailable: %s", z, x, y, e)
        raise Unreachable from e
    if len(_tiles) > 256:
        _tiles.clear()
    _tiles[key] = image
    return image


def _height(image, px, py):
    r, g, b = image.getpixel((min(TILE - 1, max(0, int(px))), min(TILE - 1, max(0, int(py)))))
    return r * 256 + g + b / 256 - 32768


def _zoom(points):
    """The most detailed zoom at which the route stays within MAX_TILES tiles."""
    length = sum(
        math.dist((a[0], a[1] * math.cos(math.radians(a[0]))), (b[0], b[1] * math.cos(math.radians(b[0]))))
        for a, b in zip(points, points[1:])
    ) * 111320
    mid_lat = math.radians(points[len(points) // 2][0])
    z = MAX_ZOOM
    while z > 6 and length / (40075016 / 2**z * math.cos(mid_lat)) * 1.5 > MAX_TILES:
        z -= 1
    return z


def _heights_at(points, z):
    """Ground height in metres at each [lat, lng] at zoom z, bilinear between pixels; None
    where Mapterhorn has no tile. Raises Unreachable when a tile could not be fetched."""
    n = 2**z
    where = []
    for lat, lng in points:
        x = (lng + 180) / 360 * n
        y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
        where.append((x, y))
    # The tiles fetched side by side, so a save waits for about one of them, not all
    keys = {(int(x), int(y)) for x, y in where}
    with ThreadPoolExecutor(max_workers=8) as pool:
        images = dict(zip(keys, pool.map(lambda k: _tile(z, *k), keys)))
    out = []
    for x, y in where:
        image = images[(int(x), int(y))]
        if image is None:
            out.append(None)
            continue
        # Pixel centres sit at half pixels: interpolate between the four around the point
        px, py = (x % 1) * TILE - 0.5, (y % 1) * TILE - 0.5
        x0, y0 = math.floor(px), math.floor(py)
        fx, fy = px - x0, py - y0
        h00, h10 = _height(image, x0, y0), _height(image, x0 + 1, y0)
        h01, h11 = _height(image, x0, y0 + 1), _height(image, x0 + 1, y0 + 1)
        top, bottom = h00 + (h10 - h00) * fx, h01 + (h11 - h01) * fx
        out.append(round(top + (bottom - top) * fy, 1))
    return out


def ground_heights(points):
    """Ground height in metres at each [lat, lng]; None where no tile could be read.
    Mapterhorn goes past zoom 12 only where it has a national lidar model, so points on a
    missing tile are read again two zooms coarser, down to its worldwide ones."""
    if not points:
        return []
    out = [None] * len(points)
    todo = list(range(len(points)))
    z = _zoom(points)
    while todo and z >= 6:
        try:
            got = _heights_at([points[i] for i in todo], z)
        except Unreachable:
            # Not to be waited on zoom after zoom: the trip is saved without heights
            break
        for i, h in zip(todo, got):
            out[i] = h
        todo = [i for i in todo if out[i] is None]
        z -= 2
    return out
