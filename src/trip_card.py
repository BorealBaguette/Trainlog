"""Render a trip as a PNG card: its route on the site's own map, ready for Discord.

The map is not redrawn here — it is rendered by MapLibre Native, from the same
`dark-train` style the site serves to browsers, by a Martin tile server:

    POST /style/{id}/static/{bbox}/{w}x{h}@2x.png

with the route as a GeoJSON overlay in the body. So the card looks like the
map on the site (labels, shields, the Rhine) and follows the style if it is
ever restyled, without a headless browser anywhere. Dark, because Discord
defaults to dark mode.

Martin needs `styles: {rendering: true}` and the style registered as a source;
set `martin.url` and `martin.style` in config.yaml to point at it. With either
missing, render_trip_card returns None and the announcement simply goes out
without a picture.

The style file Martin is given must be a *resolved* copy: the one in the repo
carries the {{mapPinUrl}} placeholder that vector_style() fills in per request,
and Martin fetches the sprite itself, so it needs a real URL in the file.

Rendering is Linux-only in Martin, uncached and non-concurrent, which suited the
handful of announcements an hour this began as. Cards are now served to anyone
with the link too (og.trip_card), so a drawn one is kept under CACHE_DIR and a
request handler only ever renders a trip nobody has asked for yet.
"""

import hashlib
import io
import json
import logging
import math
import os
from functools import lru_cache

import cairosvg
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    from fontTools.ttLib import TTFont
except ImportError:  # optional: without it there is no fallback, only Latin
    TTFont = None
from shapely.geometry import LineString

from py.utils import load_config
from src.pg import pg_session
from src.trip_types import TRIP_TYPES

logger = logging.getLogger(__name__)

FONT_FILE = "static/styles/fonts/Montserrat-Bold.ttf"

# Montserrat carries 969 codepoints — Latin, Cyrillic, Greek — so a Japanese,
# Korean, Chinese, Arabic, Thai or Hebrew station name comes out as a row of
# .notdef boxes. Pillow has no font fallback of its own, so text is split into
# runs and each run drawn with the first font that actually has the glyphs.
# These come from fonts-droid-fallback and fonts-noto-core (see Dockerfile);
# a missing file is skipped, so the cards still render without them.
FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
LOGO_ROOT = "static"

WIDTH, HEIGHT = 1200, 700
PANEL_HEIGHT = 200
MAP_HEIGHT = HEIGHT - PANEL_HEIGHT
SCALE = 2                   # Martin's @2x: a crisp card on any screen

PADDING = 0.12              # share of the route's span kept clear of the edges
MIN_SPAN = 0.02             # degrees, so a 500 m tram trip is not zoomed absurdly
TILE_SIZE = 512             # MapLibre's tile size, which sets the zoom scale
MAX_ZOOM = 13               # a two-stop trip should show its neighbourhood, not one street
MAX_LOGOS = 2
# Flights are often stored as just their two endpoints, and a straight line
# between them in lon/lat is not the path the plane flew — Berlin to Santiago
# comes out crossing Chad. Casting to geography makes ST_Segmentize follow the
# great circle, so densifying at this interval bends such a line into the real
# one. Routes that already carry a track are unchanged: their segments are
# shorter than this, so nothing is inserted.
SEGMENT_METRES = 100000

# The same Leaflet pins the trip map uses: green "play" where the trip starts,
# red "stop" where it ends, and the combined bitmap when both land on the same
# spot. Anchored at the tip, like Leaflet's own iconAnchor.
PIN_GREEN = "static/images/icons/marker-icon-2x-green.png"
PIN_RED = "static/images/icons/marker-icon-2x-red.png"
PIN_BOTH = "static/images/icons/marker-icon-2x-redGreen.png"
PIN_HEIGHT = 46             # card px; the assets are 2x for retina
PIN_MERGE_DISTANCE = 26     # closer than this on screen and one bitmap is used

TRAINLOG_LOGO = "static/images/logo_white.png"
TRAINLOG_LOGO_RATIO = 0.92  # of one band

# Country flags for the fourth line. The site ships 789 SVGs and CairoSVG is
# already a dependency; the emoji route is a dead end, since the vendored
# Twemoji build renders no ink under Pillow.
FLAG_DIR = "static/images/flags"
MAX_FLAGS = 4
# Some trips carry a mistyped year ("24" for 2024), which makes the arithmetic
# say 17 million hours. Anything past this is data, not a journey.
MAX_DURATION_HOURS = 30 * 24
FLAG_RADIUS = 0.16          # of the flag's height
FLAG_SHADOW = 0.13          # blur radius, of the flag's height
FLAG_RIM = (255, 255, 255, 90)

# The same icons the trip page puts on those figures (see the legMeta markup in
# templates/public/new_trip.html): fa-solid fa-arrows-left-right for distance,
# fa-regular fa-clock for duration. Two families, so two font files. Codepoints
# read out of font-awesome 6.5.2's own all.min.css, not guessed.
ICON_SOLID = "static/styles/fonts/fa-solid-900.ttf"
ICON_REGULAR = "static/styles/fonts/fa-regular-400.ttf"
GLYPH_DISTANCE = "\uf07e"
GLYPH_DURATION = "\uf017"
# fa-solid fa-route, as the trip page marks its line numbers with. Without it a
# bare "1" sits on that row looking like a stray character.
GLYPH_LINE = "\uf4d7"

# The text block's ink, top and bottom, measured from the panel's top edge.
# The logo chips span exactly this, so the three align however the type is
# sized. Anchoring on ink rather than on the em box matters: Montserrat leaves
# a quarter of its size as space above the capitals, which would read as the
# logos sitting low.
ROW_INK_TOP = 16
META_INK_BOTTOM = 182
META_SIZE = 38
# Operator logos own the left column, at the full height of the text block.
# The logo group may not take more than this share of the card: two wide
# logos at full block height crowded out the station names.
LOGO_MAX_WIDTH_SHARE = 0.25
# Some logos are stored tiny (Brussels Airlines is 150x35), and thumbnail()
# only ever shrinks — which is why one logo would sit at a third the height of
# its neighbours. They are enlarged to match, but only so far: past this a
# low-resolution source turns to mush and looks worse than being small.
LOGO_MAX_UPSCALE = 3.0
# Nearly every operator logo is the operator's name set as a wordmark, so
# printing the name beside it says the same thing twice. It is kept only when
# no logo could be drawn — plenty of operators have none on file.
NAME_BESIDE_LOGO = False
# Stations lead, but only just: past this they start shouting over the map.
STATION_MAX_SIZE = 38
# Below this a station name is unreadable on a phone, so it is cut instead.
STATION_MIN_SIZE = 22
META_MIN_SIZE = 22
ROUTE_FALLBACK = "#52b0fe"
RENDER_TIMEOUT = 60

# Why a card could not be drawn. NO_ROUTE and NOT_CONFIGURED are settled: asking
# again changes nothing. RENDER_FAILED is not — Martin has been OOM-killed
# mid-render and back within half a minute (deploy/martin/docker-compose.yml),
# which is why the announcer holds the trip and tries the next tick instead of
# posting a card-less announcement it can never revisit.
NO_ROUTE = "no_route"
NOT_CONFIGURED = "not_configured"
RENDER_FAILED = "render_failed"

# Drawn cards are kept on disk, like the OG ones (src/og_card.py): a render is
# a Martin round trip, Martin is a single uncached container that has been
# OOM-killed by this very work, and the same card is now served to anyone with
# the link rather than drawn once for Discord. The trip's last_modified is part
# of the file name, so an edited trip draws again instead of serving the old
# picture for ever.
CACHE_DIR = "databases/cache/trip"

# Dark panel, tuned against the style's own #343332 background.
PANEL_BG = (35, 35, 34)
PANEL_RULE = (74, 73, 71)
INK = (242, 242, 240)
MUTED = (168, 168, 164)
LATE = (232, 163, 61)       # a delay is the one number worth colouring
EARLY = (123, 191, 106)


def _martin():
    """(base_url, style_id) from config, or (None, None) when not configured."""
    config = load_config().get("martin", {})
    return config.get("url"), config.get("style")


def _fetch(trip_id):
    with pg_session() as pg:
        trip = pg.execute(
            """
            SELECT t.trip_id, t.operator, t.line_name, t.trip_type, t.trip_length,
                   t.origin_station, t.destination_station,
                   t.start_datetime, t.end_datetime,
                   t.departure_delay, t.arrival_delay, t.countries,
                   t.utc_start_datetime, t.utc_end_datetime, t.last_modified,
                   ST_AsGeoJSON(
                       ST_Segmentize(p.geom::geography, :segment)::geometry
                   ) AS route
            FROM trips t
            LEFT JOIN paths p ON p.trip_id = t.trip_id
            WHERE t.trip_id = :trip_id
            """,
            {"trip_id": trip_id, "segment": SEGMENT_METRES},
        ).fetchone()
        if trip is None or not trip["route"]:
            return None, None

        # A trip can carry several operators ("DB Fernverkehr, KVB"), and each
        # is matched through its aliases so one logged as CFF finds the SBB
        # logo (see operators/operator_aliases).
        logos = []
        names = [n.strip() for n in (trip["operator"] or "").split(",") if n.strip()]
        for name in names[:MAX_LOGOS]:
            row = pg.execute(
                """
                SELECT DISTINCT ON (a.alias) l.logo_url
                FROM operators o
                JOIN operator_aliases a ON a.operator_id = o.operator_id
                JOIN operator_logos l ON o.operator_id = l.operator_id
                WHERE a.alias = :operator
                ORDER BY a.alias, l.effective_date DESC NULLS LAST, l.uid DESC
                """,
                {"operator": name},
            ).fetchone()
            if row and os.path.exists(os.path.join(LOGO_ROOT, row["logo_url"])):
                logos.append(row["logo_url"])
    return trip, logos


def _parts(route):
    """A geometry's coordinate lists, whether it is a LineString or a Multi."""
    if route["type"] == "LineString":
        return [route["coordinates"]]
    if route["type"] == "MultiLineString":
        return route["coordinates"]
    return []


def _unwrap(parts):
    """Make longitudes continuous across the antimeridian.

    A Pacific crossing is stored jumping from +179.5 to -179.8, which reads as
    travelling 359° eastward rather than 0.7° west: the camera then spans the
    whole globe and MapLibre draws the route as a band straight across the map.
    Adding 360° at each such jump turns the run into one continuous sweep (Hong
    Kong 114°, Boston 289°). GeoJSON tolerates longitudes beyond ±180 and
    MapLibre renders them in the next world copy, which is what is wanted.
    """
    unwrapped = []
    previous_end = None
    for part in parts:
        line = []
        offset = 0.0
        previous = previous_end
        for coord in part:
            lon = coord[0] + offset
            if previous is not None:
                # Nudge whole turns until this point is the near way round.
                while lon - previous > 180:
                    offset -= 360
                    lon -= 360
                while lon - previous < -180:
                    offset += 360
                    lon += 360
            line.append([lon, coord[1]])
            previous = lon
        if line:
            unwrapped.append(line)
            previous_end = line[-1][0]
    return unwrapped


def _simplify(parts, zoom):
    """Drop detail finer than the pixels it would be drawn into.

    A dense GPS track is hundreds of kilobytes of JSON, and Martin refuses the
    POST body outright past its limit ("413 payload reached size limit"). One
    CSS pixel is 360/(512·2^zoom) degrees, so simplifying at half of that and
    rounding to the same order throws away nothing the card could show.
    """
    tolerance = 0.5 * 360 / (TILE_SIZE * 2**zoom)
    digits = max(0, min(9, math.ceil(-math.log10(tolerance)) + 1))
    simplified = []
    for part in parts:
        line = LineString([(c[0], c[1]) for c in part]) if len(part) > 1 else None
        coords = list(line.simplify(tolerance).coords) if line else part
        simplified.append([[round(x, digits), round(y, digits)] for x, y in coords])
    return simplified


def _wrap_lon(lon):
    """Back into [-180, 180) for the camera, which names a real place."""
    return ((lon + 180) % 360) - 180


def _mercator_y(lat):
    lat = max(min(lat, 85.0), -85.0)
    return math.degrees(math.log(math.tan(math.radians(45 + lat / 2))))


def _inverse_mercator_y(y):
    return math.degrees(2 * math.atan(math.exp(math.radians(y))) - math.pi / 2)


def _camera(parts):
    """The camera to ask Martin for, as "lon,lat,zoom".

    Not the bbox form: Martin renders the largest box of the requested aspect
    that fits *inside* a bbox, so a bbox drawn tightly round the route comes
    back cropped with both ends off the frame. Computing the zoom here is exact
    — MapLibre's world is 512 px at zoom 0, spanning 360° in Mercator — and it
    leaves room to cap how far a two-stop tram trip may zoom in.
    """
    lons = [c[0] for part in parts for c in part]
    lats = [c[1] for part in parts for c in part]
    min_y, max_y = _mercator_y(min(lats)), _mercator_y(max(lats))

    span_x = max(max(lons) - min(lons), MIN_SPAN) * (1 + 2 * PADDING)
    span_y = max(max_y - min_y, MIN_SPAN) * (1 + 2 * PADDING)
    centre_lon = (min(lons) + max(lons)) / 2
    centre_lat = _inverse_mercator_y((min_y + max_y) / 2)

    zoom = min(
        math.log2(WIDTH / TILE_SIZE * 360 / span_x),
        math.log2(MAP_HEIGHT / TILE_SIZE * 360 / span_y),
        MAX_ZOOM,
    )
    # The centre is returned unwrapped as well: pin placement has to measure
    # against the same longitudes the route uses, which may run past 180.
    return (
        f"{_wrap_lon(centre_lon):.5f},{centre_lat:.5f},{zoom:.3f}",
        centre_lon,
        centre_lat,
        zoom,
    )


def _project(lon, lat, centre_lon, centre_lat, zoom):
    """Where a coordinate lands on the map image, in card pixels."""
    def world(lon_, lat_):
        size = TILE_SIZE * 2**zoom
        siny = math.sin(math.radians(max(min(lat_, 85.0), -85.0)))
        return (
            (lon_ + 180) / 360 * size,
            (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * size,
        )

    x, y = world(lon, lat)
    cx, cy = world(centre_lon, centre_lat)
    return x - cx + WIDTH / 2, y - cy + MAP_HEIGHT / 2


def _overlay(parts, colour):
    """Route and its endpoints, styled with MapLibre paint property names."""
    route = {"type": "MultiLineString", "coordinates": parts}
    features = [
        # A dark casing under the line, so it reads over any landcover.
        {
            "type": "Feature",
            "geometry": route,
            "properties": {
                "line-color": "#000000", "line-opacity": 0.45, "line-width": 11,
                "line-cap": "round", "line-join": "round",
            },
        },
        {
            "type": "Feature",
            "geometry": route,
            "properties": {
                "line-color": colour, "line-width": 6,
                "line-cap": "round", "line-join": "round",
            },
        },
    ]
    return {"type": "FeatureCollection", "features": features}


def _render_map(trip):
    """((map image, unwrapped parts, camera), None), or (None, why not)."""
    base_url, style = _martin()
    if not (base_url and style):
        logger.info("No martin.url/martin.style configured; cards disabled")
        return None, NOT_CONFIGURED

    parts = _unwrap(_parts(json.loads(trip["route"])))
    if not parts or not parts[0]:
        logger.info("Trip %s has a route with no drawable parts", trip["trip_id"])
        return None, NO_ROUTE

    colour = TRIP_TYPES.get(trip["trip_type"], {}).get("colour", ROUTE_FALLBACK)
    camera, centre_lon, centre_lat, zoom = _camera(parts)
    url = (
        f"{base_url.rstrip('/')}/style/{style}/static/{camera}"
        f"/{WIDTH}x{MAP_HEIGHT}@{SCALE}x.png"
    )
    try:
        response = requests.post(
            url, json=_overlay(_simplify(parts, zoom), colour), timeout=RENDER_TIMEOUT
        )
    except requests.RequestException as e:
        logger.warning("Martin unreachable for trip %s: %s", trip["trip_id"], e)
        return None, RENDER_FAILED
    if response.status_code != 200:
        logger.warning(
            "Martin refused trip %s: %s %s",
            trip["trip_id"], response.status_code, response.text[:200],
        )
        return None, RENDER_FAILED
    image = Image.open(io.BytesIO(response.content)).convert("RGB")
    return (image, parts, (centre_lon, centre_lat, zoom)), None


def _paste_pin(card, path, x, y, scale, anchor=0.5):
    """Drop a pin bitmap with its tip at (x, y), given in card pixels."""
    try:
        pin = Image.open(path).convert("RGBA")
    except OSError as e:
        logger.warning("Trip card pin %s unusable: %s", path, e)
        return
    height = PIN_HEIGHT * scale
    width = round(pin.width * height / pin.height)
    pin = pin.resize((width, round(height)), Image.LANCZOS)
    card.alpha_composite(
        pin, (round(x * scale - width * anchor), round(y * scale - height))
    )


def _draw_pins(card, parts, camera, scale):
    """Start and end markers, matching the pins on the site's own trip map."""
    centre_lon, centre_lat, zoom = camera
    start = _project(*parts[0][0][:2], centre_lon, centre_lat, zoom)
    end = _project(*parts[-1][-1][:2], centre_lon, centre_lat, zoom)

    if math.dist(start, end) < PIN_MERGE_DISTANCE:
        # A there-and-back trip: the site draws one combined pin rather than
        # stacking two on the same point.
        _paste_pin(card, PIN_BOTH, *end, scale)
        return
    _paste_pin(card, PIN_RED, *end, scale)
    _paste_pin(card, PIN_GREEN, *start, scale)


def _fit_logo(logo, cell_width, cell_height):
    """Scale a logo into a cell, never past LOGO_MAX_UPSCALE.

    Some logos are stored tiny (Brussels Airlines is 150x35), so this enlarges
    as well as shrinks — but only so far, since past that a low-resolution
    source turns to mush and looks worse than being small.
    """
    factor = min(
        cell_width / logo.width, cell_height / logo.height, LOGO_MAX_UPSCALE
    )
    return logo.resize(
        (max(1, round(logo.width * factor)), max(1, round(logo.height * factor))),
        Image.LANCZOS,
    )


def _logo_grid(images, max_width, max_height, chip, gap):
    """Arrange logos in the grid that fills the allowance best.

    Two wide, short logos side by side waste most of their height; stacked they
    are twice as big. Two tall ones are the other way round. Rather than guess,
    every rows x cols shape is measured and the one with the most drawn logo
    area wins. Almost always one or two logos, so the search is trivial.
    """
    best = None
    for rows in range(1, len(images) + 1):
        cols = math.ceil(len(images) / rows)
        cell_width = (max_width - gap * (cols - 1)) / cols - 2 * chip
        cell_height = (max_height - gap * (rows - 1)) / rows - 2 * chip
        if cell_width <= 0 or cell_height <= 0:
            continue
        placed = [_fit_logo(image, cell_width, cell_height) for image in images]
        area = sum(image.width * image.height for image in placed)
        if best is None or area > best[0]:
            best = (area, rows, cols, placed)
    if best is None:
        return [], 0
    _, rows, cols, placed = best

    # Lay the grid out row by row. Every chip in a row is the full height of
    # that row and every chip in a column the same width, so a square logo and
    # a wide strip still line up on all four edges — stacked chips of unequal
    # width read as a mistake.
    row_height = (max_height - gap * (rows - 1)) / rows
    column_widths = [
        max((placed[row * cols + col].width
             for row in range(rows) if row * cols + col < len(placed)), default=0)
        + 2 * chip
        for col in range(cols)
    ]

    boxes = []
    for row in range(rows):
        y = row * (row_height + gap)
        x = 0
        for col, image in enumerate(placed[row * cols:(row + 1) * cols]):
            boxes.append((x, y, column_widths[col], row_height, image))
            x += column_widths[col] + gap
    return boxes, sum(column_widths) + gap * (cols - 1)


def _country_metres(value):
    """How far a trip ran through one country, whichever shape the entry has.

    trips.countries is written two ways: {"FR": 100} and, once a trip's
    electrification is known, {"FR": {"elec": 50, "nonelec": 50}}. Sorting on
    the raw value compares dict with dict and raises, which is what stopped
    trip 927427 from drawing a card at all. Same reading as carbon.py and
    leaderboards.py, which have both formats to handle too.
    """
    if isinstance(value, dict):
        return sum(v or 0 for v in value.values())
    return value or 0


def _flags(countries):
    """Country flags for a trip, widest stretch first, as RGBA images.

    Rasterised from the SVGs the site already ships rather than drawn as emoji:
    the vendored Twemoji build produces no ink under Pillow.
    """
    if isinstance(countries, str):
        try:
            countries = json.loads(countries)
        except ValueError:
            return []
    if not countries:
        return []
    ordered = sorted(
        countries.items(), key=lambda item: _country_metres(item[1]), reverse=True
    )
    return [code.lower() for code, _ in ordered[:MAX_FLAGS]]


def _flag_image(code, height):
    """One flag, rounded and with a soft drop shadow to lift it off the panel."""
    path = os.path.join(FLAG_DIR, f"{code}.svg")
    if not os.path.exists(path):
        return None
    try:
        png = cairosvg.svg2png(url=path, output_height=height)
    except Exception as e:  # a malformed SVG must not cost the whole card
        logger.warning("Flag %s unusable: %s", code, e)
        return None
    flag = Image.open(io.BytesIO(png)).convert("RGBA")

    radius = max(2, round(height * FLAG_RADIUS))
    mask = Image.new("L", flag.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, flag.width - 1, flag.height - 1], radius=radius, fill=255
    )
    flag.putalpha(mask)

    # Composited on a transparent canvas with room for the shadow, so the
    # caller can paste one image and get both.
    # A hairline rim: on a dark panel a flag with dark edges (NL, DE) otherwise
    # dissolves into the background whatever the shadow does.
    ImageDraw.Draw(flag).rounded_rectangle(
        [0, 0, flag.width - 1, flag.height - 1],
        radius=radius, outline=FLAG_RIM, width=max(1, round(height * 0.045)),
    )

    blur = max(1, round(height * FLAG_SHADOW))
    pad = blur * 2
    canvas = Image.new("RGBA", (flag.width + 2 * pad, flag.height + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 220), (pad, pad + blur), mask)
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))
    canvas.alpha_composite(flag, (pad, pad))
    return canvas


def _duration(trip):
    """"4 h 30", preferring UTC so a flight across time zones is not mis-timed."""
    start = trip["utc_start_datetime"] or trip["start_datetime"]
    end = trip["utc_end_datetime"] or trip["end_datetime"]
    if not (start and end) or end <= start:
        return ""
    minutes = round((end - start).total_seconds() / 60)
    hours, minutes = divmod(minutes, 60)
    if hours > MAX_DURATION_HOURS:
        return ""
    return f"{hours} h {minutes:02d}" if hours else f"{minutes} min"


def _strip_flag(name):
    """Station names are stored as "🇩🇪 Köln Hbf"; Montserrat cannot draw the flag."""
    if not name:
        return ""
    while name and "\U0001F1E6" <= name[0] <= "\U0001F1FF":
        name = name[1:]
    return name.strip()


def _ellipsize(draw, text, size, max_width):
    """Cut a string to fit, with an ellipsis. Some station names are essays."""
    if _text_length(draw, text, size) <= max_width:
        return text
    ellipsis = "…"
    while text and _text_length(draw, text + ellipsis, size) > max_width:
        text = text[:-1]
    return text.rstrip() + ellipsis


def _fit(draw, text, size, max_width, min_size=None):
    """Largest size at or below `size` that keeps `text` inside max_width.

    Stops at min_size: one user's "line name" is a 219-character sentence, and
    shrinking until that fits produces a line nobody can read. The caller
    ellipsizes instead.
    """
    floor = min_size if min_size is not None else 12
    while size > floor:
        if _text_length(draw, text, size) <= max_width:
            return size
        size -= 2
    return size


@lru_cache(maxsize=None)
def _coverage(path):
    """The codepoints a font file actually has glyphs for."""
    if TTFont is None:
        return frozenset()
    try:
        return frozenset(TTFont(path, fontNumber=0, lazy=True).getBestCmap())
    except Exception as e:
        logger.warning("Font %s unreadable: %s", path, e)
        return frozenset()


@lru_cache(maxsize=None)
def _font_chain():
    """Montserrat first, then whichever fallbacks exist on this machine.

    Only Montserrat when fontTools is missing: without a cmap to read there is
    no way to tell which font has a glyph, so falling back would be guesswork.
    Non-Latin names come out as boxes, which is how it behaved before — a
    cosmetic loss, not a reason to fail.
    """
    if TTFont is None:
        return (FONT_FILE,)
    return tuple(
        path for path in [FONT_FILE, *FALLBACK_FONTS] if os.path.exists(path)
    )


@lru_cache(maxsize=256)
def _font(path, size):
    return ImageFont.truetype(path, size)


def _font_for(char):
    """First font in the chain with a glyph for this character."""
    chain = _font_chain()
    for path in chain:
        if ord(char) in _coverage(path):
            return path
    return chain[0]


def _runs(text, size):
    """Split text into (font, run) pairs, one per stretch sharing a font."""
    runs = []
    for char in text:
        path = _font_for(char)
        if runs and runs[-1][0] == path:
            runs[-1][1].append(char)
        else:
            runs.append((path, [char]))
    return [(_font(path, size), "".join(chars)) for path, chars in runs]


def _text_length(draw, text, size):
    return sum(draw.textlength(run, font=font) for font, run in _runs(text, size))


def _draw_text(draw, xy, text, size, fill):
    x, y = xy
    for font, run in _runs(text, size):
        draw.text((x, y), run, font=font, fill=fill)
        x += draw.textlength(run, font=font)


def _delay(seconds):
    """"(+35)" in minutes, or "" when a trip ran to time."""
    if not seconds:
        return ""
    minutes = round(seconds / 60)
    return f"({minutes:+d})" if minutes else ""


def _row_layout(draw, rows, left, right, scale, max_size):
    """Largest font at which both station rows fit, with their time column.

    Both rows are measured together: sizing each to its own width put "Bahnhof
    Mülheim" at full size above a half-height "Frankfurt am Main Airport Long
    distance trains", which reads as a mistake. The pair is one thing, so the
    longer name sets the size for both, and for the times beside them.

    Returns (font size in device pixels, x where the station names start).
    """
    size = STATION_MIN_SIZE
    station_x = left
    for size in range(max_size, STATION_MIN_SIZE - 1, -2):
        points = size * scale
        gap = _text_length(draw, " ", points)
        column = 0
        for when, delay_seconds, _ in rows:
            cell = _text_length(draw, when.strftime("%H:%M"), points) if when else 0
            delay = _delay(delay_seconds)
            if delay:
                cell += gap + _text_length(draw, delay, points)
            column = max(column, cell)
        station_x = left + (column + 18 * scale if column else 0)
        if all(
            _text_length(draw, _strip_flag(station), points) <= right - station_x
            for _, _, station in rows
        ):
            return points, station_x
    # Nothing fit even at the smallest size: keep the last station_x, which is
    # past the time column, so the name is truncated rather than drawn over it.
    return size * scale, station_x


def _draw_panel(card, trip, logos, scale):
    """Four evenly spaced lines beside the operator logos.

    origin / destination / operator + distance / countries + date + duration.
    The block is divided into equal bands and each line centred in its own, so
    the logo chips, which span the whole block, contain exactly four lines.
    """
    draw = ImageDraw.Draw(card)
    top = MAP_HEIGHT * scale
    height = PANEL_HEIGHT * scale
    pad = 22 * scale
    draw.rectangle([0, top, WIDTH * scale, HEIGHT * scale], fill=PANEL_BG)
    draw.line([0, top, WIDTH * scale, top], fill=PANEL_RULE, width=2 * scale)

    ink_top = top + ROW_INK_TOP * scale
    ink_bottom = top + META_INK_BOTTOM * scale
    band = (ink_bottom - ink_top) / 4

    def centre(index):
        return ink_top + band * (index + 0.5)

    def draw_line(x, index, text, size, fill):
        """Vertically centre on a band, measured on "Hg" so every line on the
        same band shares a baseline whatever glyphs it happens to contain."""
        reference = _font(FONT_FILE, size).getbbox("Hg")
        _draw_text(draw, (x, centre(index) - (reference[1] + reference[3]) / 2),
                   text, size, fill)

    # --- left column: the operator logos, inside their width allowance ----
    left = pad
    images = []
    for logo_url in logos:
        try:
            images.append(Image.open(os.path.join(LOGO_ROOT, logo_url)).convert("RGBA"))
        except OSError as e:
            logger.warning("Trip card logo %s unusable: %s", logo_url, e)
    if images:
        chip = 7 * scale
        gap = 12 * scale
        boxes, group_width = _logo_grid(
            images, WIDTH * scale * LOGO_MAX_WIDTH_SHARE, ink_bottom - ink_top,
            chip, gap,
        )
        for x, y, cell_width, row_height, image in boxes:
            x, y = left + x, ink_top + y
            # Logos are drawn for light backgrounds, so each gets a white chip.
            draw.rounded_rectangle(
                [x, y, x + cell_width, y + row_height],
                radius=8 * scale, fill=(255, 255, 255),
            )
            card.paste(
                image,
                (round(x + (cell_width - image.width) / 2),
                 round(y + (row_height - image.height) / 2)),
                image,
            )
        left += group_width + 20 * scale

    right = WIDTH * scale - pad

    # --- last band, right: the Trainlog mark ------------------------------
    info_right = right
    try:
        mark = Image.open(TRAINLOG_LOGO).convert("RGBA")
        mark_height = round(band * TRAINLOG_LOGO_RATIO)
        mark = mark.resize(
            (round(mark.width * mark_height / mark.height), mark_height), Image.LANCZOS
        )
        card.alpha_composite(
            mark, (round(right - mark.width), round(centre(3) - mark_height / 2))
        )
        info_right = right - mark.width - 22 * scale
    except OSError as e:
        logger.warning("Trainlog logo unusable: %s", e)

    meta_right = right

    # --- the two stations, running to the card's right edge ---------------
    # The distance and the mark are below them, not beside, so the names have
    # the full width and only shrink when they genuinely do not fit.
    rows = [
        (trip["start_datetime"], trip["departure_delay"], trip["origin_station"]),
        (trip["end_datetime"], trip["arrival_delay"], trip["destination_station"]),
    ]
    size, station_x = _row_layout(
        draw, rows, left, right, scale,
        max_size=min(STATION_MAX_SIZE, int(band / scale / 0.8)),
    )

    for index, (when, delay_seconds, station) in enumerate(rows):
        x = left
        if when:
            text = when.strftime("%H:%M")
            draw_line(x, index, text, size, INK)
            x += _text_length(draw, text + " ", size)
        delay = _delay(delay_seconds)
        if delay:
            draw_line(x, index, delay, size, LATE if delay_seconds > 0 else EARLY)
        draw_line(station_x, index,
                  _ellipsize(draw, _strip_flag(station), size, right - station_x),
                  size, INK)

    # No ceiling relative to the stations: the muted colour already ranks this
    # below them, so it only shrinks when it does not fit the width.
    parts = [trip["operator"], trip["line_name"]]
    line_only = images and not NAME_BESIDE_LOGO and trip["line_name"]
    if line_only:
        parts = [trip["line_name"]]
    headline = " · ".join(p for p in parts if p)
    if headline:
        x = left
        if line_only:
            # The logo says who; this says which service. On its own a line
            # number reads as a stray character, so it carries the route icon.
            icon = ImageFont.truetype(ICON_SOLID, round(META_SIZE * scale * 0.78))
            box = icon.getbbox(GLYPH_LINE)
            draw.text((x - box[0], centre(2) - (box[1] + box[3]) / 2),
                      GLYPH_LINE, font=icon, fill=MUTED)
            x += (box[2] - box[0]) + 14 * scale
        headline_size = _fit(draw, headline, META_SIZE * scale, meta_right - x,
                             min_size=META_MIN_SIZE * scale)
        draw_line(x, 2, _ellipsize(draw, headline, headline_size, meta_right - x),
                  headline_size, MUTED)

    # --- fourth band: the figures on the left, the flags over by the mark --
    # Kept apart deliberately: flag, icon, number, icon, number in one run
    # reads as three of the same kind of thing, which the flag is not.
    icon_size = round(META_SIZE * scale * 0.82)
    facts = []
    if trip["trip_length"]:
        distance = f"{round(trip['trip_length'] / 1000):,} km".replace(",", " ")
        facts.append((ImageFont.truetype(ICON_SOLID, icon_size), GLYPH_DISTANCE, distance))
    duration = _duration(trip)
    if duration:
        facts.append((ImageFont.truetype(ICON_REGULAR, icon_size), GLYPH_DURATION, duration))

    x = left
    for icon_font, glyph, text in facts:
        box = icon_font.getbbox(glyph)
        draw.text((x - box[0], centre(3) - (box[1] + box[3]) / 2),
                  glyph, font=icon_font, fill=MUTED)
        x += (box[2] - box[0]) + 12 * scale
        draw_line(x, 3, text, META_SIZE * scale, MUTED)
        x += _text_length(draw, text, META_SIZE * scale) + 26 * scale

    flag_height = round(band * 0.78)
    flags = [image for image in
             (_flag_image(code, flag_height) for code in _flags(trip["countries"]))
             if image is not None]
    if flags:
        total = sum(image.width for image in flags) + 2 * scale * (len(flags) - 1)
        fx = max(x + 24 * scale, info_right - total)
        for image in flags:
            card.alpha_composite(image, (round(fx), round(centre(3) - image.height / 2)))
            fx += image.width + 2 * scale


def _cache_path(trip):
    """databases/cache/trip/<trip id>-<what the trip currently is>.png"""
    fingerprint = hashlib.sha256(
        f"{trip['last_modified']}".encode()
    ).hexdigest()[:8]
    return os.path.join(CACHE_DIR, f"{trip['trip_id']}-{fingerprint}.png")


def render_trip_card(trip_id):
    """(PNG bytes, None), or (None, why there is no card).

    The reason is the point of the pair: a caller that can wait — the announcer,
    which has a fifteen-minute window and a tick every minute — needs to tell a
    renderer that is down and will come back (RENDER_FAILED) from a trip that
    has nothing to draw and never will (NO_ROUTE).
    """
    trip, logos = _fetch(trip_id)
    if trip is None:
        logger.info("Trip %s has no route to draw a card from", trip_id)
        return None, NO_ROUTE

    path = _cache_path(trip)
    if os.path.exists(path):
        with open(path, "rb") as handle:
            return handle.read(), None

    rendered, reason = _render_map(trip)
    if rendered is None:
        return None, reason
    map_image, parts, camera = rendered

    scale = map_image.width // WIDTH or 1
    card = Image.new("RGBA", (WIDTH * scale, HEIGHT * scale), PANEL_BG + (255,))
    card.paste(map_image, (0, 0))
    _draw_pins(card, parts, camera, scale)
    _draw_panel(card, trip, logos, scale)

    out = io.BytesIO()
    card.convert("RGB").save(out, format="PNG", optimize=True)
    png = out.getvalue()

    # Written through a temporary file: a reader that arrives mid-write must
    # find either the old card or the new one, never half a PNG.
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(png)
    os.replace(tmp, path)
    return png, None
