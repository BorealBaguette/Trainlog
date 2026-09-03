"""The picture a shared Trainlog link unfurls into.

Same renderer as the Discord trip card (src/trip_card) and deliberately
simpler: a link preview is shown small and next to the poster's own text, so it
carries the route and a single caption line rather than the card's four rows of
operator detail. It also covers *sets* of trips, since a month, a tag or a
multi-trip selection all share one URL for many.

Rendering goes through Martin, which is slow and non-concurrent, so every card
is written to databases/cache and served from there afterwards. The cache key
is the trip ids and the caption, both of which change when the page does, so
nothing has to expire it.
"""

import hashlib
import io
import json
import logging
import os

import requests
from PIL import Image, ImageDraw

from src.pg import pg_session
from src.trip_card import (
    INK,
    MAP_HEIGHT,
    MUTED,
    PANEL_BG,
    PANEL_RULE,
    SEGMENT_METRES,
    TRAINLOG_LOGO,
    WIDTH,
    _camera,
    _draw_text,
    _ellipsize,
    _fit,
    _flag_image,
    _martin,
    _overlay,
    _parts,
    _simplify,
    _text_length,
    _unwrap,
)
from src.trip_types import TRIP_TYPES

logger = logging.getLogger(__name__)

BAR_HEIGHT = 130
HEIGHT = MAP_HEIGHT + BAR_HEIGHT     # 1200x630, the size every unfurler wants
SCALE = 2
RENDER_TIMEOUT = 60
ROUTE_FALLBACK = "#52b0fe"

TITLE_SIZE = 46
SUBTITLE_SIZE = 32
# What the two halves of an origin -> destination title drop to once it has to
# wrap, so three lines still sit inside the bar. Only the long titles pay this:
# a title that fits on one line keeps the full size.
TITLE_WRAP_SIZE = 34
SUBTITLE_WRAP_SIZE = 28
MIN_SIZE = 24
LEADING = 1.2
PAD = 26
LOGO_RATIO = 0.42                    # of the bar

# A month of commuting is a thousand trips, and asking Martin to draw them all
# would time out for a picture nobody can read anyway. The longest are kept:
# they are the ones that set the frame.
MAX_TRIPS = 120
MAX_FLAGS = 6

CACHE_DIR = "databases/cache/og"


def _fetch(trip_ids):
    """(trip_type, route GeoJSON) for the public trips among these ids.

    Public only, and screened here rather than by the caller: this image is
    fetched by crawlers with no session at all, so a private trip must not be
    drawable by anyone who can guess its id.
    """
    with pg_session() as pg:
        return pg.execute(
            """
            SELECT t.trip_type,
                   ST_AsGeoJSON(
                       ST_Segmentize(p.geom::geography, :segment)::geometry
                   ) AS route
            FROM trips t
            JOIN paths p ON p.trip_id = t.trip_id
            WHERE t.trip_id = ANY(:ids)
              AND t.visibility = 'public'
              AND p.geom IS NOT NULL
            ORDER BY t.trip_length DESC NULLS LAST
            LIMIT :limit
            """,
            {"ids": list(trip_ids), "segment": SEGMENT_METRES, "limit": MAX_TRIPS},
        ).fetchall()


def _geometry(rows):
    """(all parts, {colour: parts}) with longitudes made continuous.

    Unwrapping is done over every trip at once so a set that crosses the
    antimeridian is framed as one sweep rather than as two halves of the globe.
    """
    lengths, colours = [], []
    everything = []
    for row in rows:
        parts = _parts(json.loads(row["route"]))
        colour = TRIP_TYPES.get(row["trip_type"], {}).get("colour") or ROUTE_FALLBACK
        lengths.append(len(parts))
        colours.append(colour)
        everything.extend(parts)

    everything = _unwrap(everything)

    by_colour = {}
    index = 0
    for count, colour in zip(lengths, colours):
        by_colour.setdefault(colour, []).extend(everything[index:index + count])
        index += count
    return everything, by_colour


def _render_map(rows):
    """(map image, camera) or None when Martin cannot serve it."""
    base_url, style = _martin()
    if not (base_url and style):
        logger.info("No martin.url/martin.style configured; OG cards disabled")
        return None

    parts, by_colour = _geometry(rows)
    if not parts or not parts[0]:
        return None

    camera, centre_lon, centre_lat, zoom = _camera(parts)
    features = []
    for colour, coloured in by_colour.items():
        features += _overlay(_simplify(coloured, zoom), colour)["features"]

    url = (
        f"{base_url.rstrip('/')}/style/{style}/static/{camera}"
        f"/{WIDTH}x{MAP_HEIGHT}@{SCALE}x.png"
    )
    try:
        response = requests.post(
            url,
            json={"type": "FeatureCollection", "features": features},
            timeout=RENDER_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.warning("Martin unreachable for OG card: %s", e)
        return None
    if response.status_code != 200:
        logger.warning(
            "Martin refused OG card: %s %s", response.status_code, response.text[:200]
        )
        return None
    return Image.open(io.BytesIO(response.content)).convert("RGB")


def _plain(text):
    """Drop the flags stored in station names ("🇩🇪 Köln Hbf").

    The bar draws its own flags from the SVGs the site ships; Montserrat has no
    regional-indicator glyph, so one left in the caption prints as two boxes.
    Unlike the trip card's version these can sit mid-string, in the middle of an
    origin -> destination title.
    """
    stripped = "".join(
        char for char in (text or "") if not "\U0001F1E6" <= char <= "\U0001F1FF"
    )
    return " ".join(stripped.split())


def _title_lines(draw, title, width, scale):
    """The title as one line, or as its two halves when one would be clipped.

    Station names run long ("Frankfurt (Main) Flughafen Fernbahnhof"), and on a
    single line it is always the destination that loses its tail to the
    ellipsis — the half the reader is least likely to be able to guess. Broken
    at the arrow, each end gets a line of its own.
    """
    if not title:
        return [], 0
    size = TITLE_SIZE * scale
    if _text_length(draw, title, size) <= width:
        return [title], size

    origin, arrow, destination = title.partition("→")
    if not arrow:
        # A period or a tag name: nothing to break on, so it shrinks and, past
        # MIN_SIZE, is ellipsized by the caller.
        return [title], _fit(draw, title, size, width, min_size=MIN_SIZE * scale)

    # The arrow goes with it. On one line it sits between the two names and
    # balances; carried onto the second it hangs off one end only, and reads as
    # a mark against the arrival rather than as the join between the pair.
    # Stacked, the order says the same thing.
    lines = [origin.strip(), destination.strip()]
    return lines, min(
        _fit(draw, line, TITLE_WRAP_SIZE * scale, width, min_size=MIN_SIZE * scale)
        for line in lines
    )


def _draw_bar(card, title, subtitle, countries, scale):
    """Title over subtitle on the left, the Trainlog mark and flags on the right."""
    draw = ImageDraw.Draw(card)
    top = MAP_HEIGHT * scale
    pad = PAD * scale
    draw.rectangle([0, top, WIDTH * scale, HEIGHT * scale], fill=PANEL_BG)
    draw.line([0, top, WIDTH * scale, top], fill=PANEL_RULE, width=2 * scale)

    right = WIDTH * scale - pad
    try:
        mark = Image.open(TRAINLOG_LOGO).convert("RGBA")
        height = round(BAR_HEIGHT * scale * LOGO_RATIO)
        mark = mark.resize(
            (round(mark.width * height / mark.height), height), Image.LANCZOS
        )
        card.alpha_composite(
            mark,
            (round(right - mark.width),
             round(top + (BAR_HEIGHT * scale - mark.height) / 2)),
        )
        right -= mark.width + 24 * scale
    except OSError as e:
        logger.warning("Trainlog logo unusable: %s", e)

    flag_height = round(BAR_HEIGHT * scale * 0.3)
    flags = [
        image for image in
        (_flag_image(code.lower(), flag_height) for code in countries[:MAX_FLAGS])
        if image is not None
    ]
    if flags:
        total = sum(image.width for image in flags) + 2 * scale * (len(flags) - 1)
        x = right - total
        for image in flags:
            card.alpha_composite(
                image,
                (round(x), round(top + (BAR_HEIGHT * scale - image.height) / 2)),
            )
            x += image.width + 2 * scale
        right -= total + 24 * scale

    # The text block shares the left column and whatever the mark and flags left
    # it. The station flags stored in the names ("🇩🇪 Köln Hbf") come off first:
    # the bar draws flags itself from the SVGs, and Montserrat has no
    # regional-indicator glyph, so left in they would print as boxes.
    width = right - pad
    title, subtitle = _plain(title), _plain(subtitle)

    lines, title_size = _title_lines(draw, title, width, scale)
    block = [(line, title_size, INK) for line in lines]
    if subtitle:
        wrapped = len(lines) > 1
        block.append((
            subtitle,
            (SUBTITLE_WRAP_SIZE if wrapped else SUBTITLE_SIZE) * scale,
            MUTED,
        ))
    if not block:
        return

    # Centred as one block rather than hung from a fixed top, so two lines and
    # three sit equally well in the bar.
    height = sum(size * LEADING for _, size, _ in block[:-1]) + block[-1][1]
    y = top + (BAR_HEIGHT * scale - height) / 2
    for text, size, fill in block:
        _draw_text(draw, (pad, y), _ellipsize(draw, text, size, width), size, fill)
        y += size * LEADING


def _cache_path(name, contents) -> str:
    """databases/cache/og/<what the URL asked for>-<what it resolved to>.jpg

    The name is the URL's own segment — a share key, a period, a tag uuid — so
    a file on disk can be traced straight back to the link that made it. The
    suffix is what that name currently means: a period gains trips through the
    month and a tag gains members, so a name alone would serve the first render
    of October for the rest of it.
    """
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in name)
    fingerprint = hashlib.sha256(contents.encode()).hexdigest()[:8]
    return os.path.join(CACHE_DIR, f"{safe[:64]}-{fingerprint}.jpg")


def _fetch_plan(plan_uuid):
    """(trip_type, route GeoJSON) for the legs of a plan.

    Plans live in their own tables — a plan leg carries its path inline rather
    than in `paths` — so they cannot go through _fetch. Visibility is the
    plan's, checked by the caller against the owner's profile: a plan leg has
    no per-trip visibility of its own.
    """
    with pg_session() as pg:
        return pg.execute(
            """
            SELECT pt.trip_type,
                   ST_AsGeoJSON(
                       ST_Segmentize(pt.geom::geography, :segment)::geometry
                   ) AS route
            FROM plan_trips pt
            JOIN plans p ON p.uid = pt.plan_id
            WHERE p.uuid = :uuid AND pt.geom IS NOT NULL
            ORDER BY pt.trip_length DESC NULLS LAST
            LIMIT :limit
            """,
            {"uuid": plan_uuid, "segment": SEGMENT_METRES, "limit": MAX_TRIPS},
        ).fetchall()


def _card(name, key, fetch, title, subtitle, countries):
    """PNG bytes for whatever `fetch` returns, from cache when already drawn.

    None when nothing is drawable — no route to draw, or no renderer — and the
    caller falls back to the static logo.
    """
    path = _cache_path(name, key)
    if os.path.exists(path):
        with open(path, "rb") as handle:
            return handle.read()

    rows = fetch()
    if not rows:
        return None
    map_image = _render_map(rows)
    if map_image is None:
        return None

    scale = map_image.width // WIDTH or 1
    card = Image.new("RGBA", (WIDTH * scale, HEIGHT * scale), PANEL_BG + (255,))
    card.paste(map_image, (0, 0))
    _draw_bar(card, title, subtitle, list(countries), scale)

    # JPEG, not PNG: past roughly 600KB WhatsApp stops unfurling a card at all
    # and falls back to a small square thumbnail, and a 2x map is 600KB as PNG
    # but 260KB at q90. Kept at the full 2x rather than resampled down to the
    # 1200x630 of the tags — the clients that were working keep the detail they
    # had, and the size problem was the format.
    out = io.BytesIO()
    card.convert("RGB").save(out, format="JPEG", quality=90, optimize=True, progressive=True)
    png = out.getvalue()

    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(png)
    os.replace(tmp, path)
    return png


def render_og_card(name, trip_ids, title, subtitle="", countries=()):
    """PNG bytes for these trips, from cache when it has been drawn before."""
    ids = sorted(set(trip_ids))
    return _card(
        name,
        f"{ids}|{title}|{subtitle}|{list(countries)}",
        lambda: _fetch(ids),
        title,
        subtitle,
        countries,
    )


def render_plan_og_card(plan_uuid, title, subtitle="", countries=(), version=""):
    """PNG bytes for a plan's legs.

    `version` is what the plan currently is (its latest edit) — a plan gains and
    loses legs, so the uuid alone would serve the first render of it forever.
    """
    return _card(
        f"plan-{plan_uuid}",
        f"{plan_uuid}|{version}|{title}|{subtitle}|{list(countries)}",
        lambda: _fetch_plan(plan_uuid),
        title,
        subtitle,
        countries,
    )
