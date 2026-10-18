import json
import re
import time

import requests

from py.utils import load_config

# Mirrors knownStyles in https://openrailwaymap.app/js/ui.js
PRESETS = {
    "standard": {
        "tracks": "usage", "stations": "station",
        "pois": ["radio", "facility", "equipment", "level_crossing", "train_protection"],
        "turntables": "plain", "platforms": "plain", "substations": "none", "boxes": "none",
        "catenaries": "none", "switches": "plain", "signals": "none",
    },
    "speed": {
        "tracks": "speed", "stations": "none", "pois": [], "turntables": "none",
        "platforms": "none", "substations": "none", "boxes": "none", "catenaries": "none",
        "switches": "none", "signals": "speed",
    },
    "signals": {
        "tracks": "train_protection", "stations": "none",
        "pois": ["vacancy_detection", "train_protection"], "turntables": "none",
        "platforms": "none", "substations": "none", "boxes": "plain", "catenaries": "none",
        "switches": "none", "signals": "signals",
    },
    "electrification": {
        "tracks": "voltage_frequency", "stations": "none", "pois": ["electrical_equipment"],
        "turntables": "none", "platforms": "none", "substations": "plain", "boxes": "none",
        "catenaries": "plain", "switches": "none", "signals": "electrification",
    },
    "track": {
        "tracks": "gauge", "stations": "none", "pois": [], "turntables": "none",
        "platforms": "none", "substations": "none", "boxes": "none", "catenaries": "none",
        "switches": "none", "signals": "none",
    },
    "operator": {
        "tracks": "operator", "stations": "operator", "pois": ["operator"],
        "turntables": "none", "platforms": "none", "substations": "none", "boxes": "operator",
        "catenaries": "none", "switches": "none", "signals": "none",
    },
}

_CACHE_TTL = 24 * 3600
_cache = {"style": None, "fetched": 0}


def _upstream_style():
    if _cache["style"] is None or time.time() - _cache["fetched"] > _CACHE_TTL:
        try:
            resp = requests.get(f"{tiles_url()}/style.json", timeout=10)
            resp.raise_for_status()
            _cache["style"] = resp.json()
        except (requests.RequestException, ValueError):
            # Keep serving the last good style; retry on the next request
            if _cache["style"] is None:
                raise
            return _cache["style"]
        _cache["fetched"] = time.time()
    return _cache["style"]


HOVER = ["boolean", ["feature-state", "hover"], False]


def _drop_hover(expr):
    # openrailwaymap.app highlights hovered lines; we never set that state, and paint depending
    # on feature-state makes MapLibre track and re-check it for every feature
    if isinstance(expr, list):
        if expr and expr[0] == "case":
            pairs = [(c, v) for c, v in zip(expr[1:-1:2], expr[2:-1:2]) if c != HOVER]
            if not pairs:
                return _drop_hover(expr[-1])
            expr = ["case", *(e for pair in pairs for e in pair), expr[-1]]
        return [_drop_hover(e) for e in expr]
    if isinstance(expr, dict):
        return {k: _drop_hover(v) for k, v in expr.items()}
    return expr


def _substitute(expr, state):
    # MapLibre 5.6 can't evaluate global-state in layout.visibility, so bake the preset in
    if isinstance(expr, list):
        if len(expr) == 2 and expr[0] == "global-state":
            return ["literal", state[expr[1]]]
        if expr and expr[0] == "literal":
            return expr
        return [_substitute(e, state) for e in expr]
    if isinstance(expr, dict):
        return {k: _substitute(v, state) for k, v in expr.items()}
    return expr


def _evaluate(expr):
    if not isinstance(expr, list):
        return expr
    op, args = expr[0], expr[1:]
    if op == "literal":
        return args[0]
    if op == "case":
        for cond, result in zip(args[:-1:2], args[1:-1:2]):
            if _evaluate(cond):
                return _evaluate(result)
        return _evaluate(args[-1])
    if op == "all":
        return all(_evaluate(a) for a in args)
    if op == "any":
        return any(_evaluate(a) for a in args)
    values = [_evaluate(a) for a in args]
    return {
        "!": lambda: not values[0],
        "==": lambda: values[0] == values[1],
        "!=": lambda: values[0] != values[1],
        "<": lambda: values[0] < values[1],
        ">": lambda: values[0] > values[1],
        "in": lambda: values[0] in values[1],
        "length": lambda: len(values[0]),
    }[op]()


# Upstream's proposed dots ([1, 4]) read as sparse once widened
DASH_OVERRIDES = {"proposed": ["literal", [1, 2]]}


def _split_dasharray(layer):
    # MapLibre 5.6 has no data-driven line-dasharray: emit one layer per ["match", ["get", "state"], …] branch
    dash = layer.get("paint", {}).get("line-dasharray")
    if not (isinstance(dash, list) and dash[:2] == ["match", ["get", "state"]]):
        return [layer]

    branches = dash[2:-1]
    labels, outputs = branches[0::2], branches[1::2]
    all_labels = [s for label in labels for s in (label if isinstance(label, list) else [label])]
    cases = [(["in", ["get", "state"], ["literal", label if isinstance(label, list) else [label]]],
              DASH_OVERRIDES.get(label, output))
             for label, output in zip(labels, outputs)]
    cases.append((["!", ["in", ["get", "state"], ["literal", all_labels]]], dash[-1]))

    split = []
    for i, (condition, output) in enumerate(cases):
        filter_ = ["all", layer["filter"], condition] if "filter" in layer else condition
        split.append({**layer, "id": f"{layer['id']}_{i}", "filter": filter_,
                      "paint": {**layer["paint"], "line-dasharray": output}})
    return split


HALO_WIDTH = 1
# Match openrailwaymap.org's raster lines. Measured on its tiles, in MapLibre zooms (one less
# than its 256px tile zooms): main lines about 2px up to zoom 4, 3px to 8, 4px from 9, plus a 1px
# white edge. Upstream's widths grow unevenly, hence a factor per zoom range
WIDTH_SCALES = {0: 1.8, 5: 2, 7: 1.5, 9: 2}


def _width_scale(z):
    return WIDTH_SCALES[max(k for k in WIDTH_SCALES if k <= z)]


def _times(expr, factor, extra):
    if isinstance(expr, (int, float)):
        return expr * factor + extra
    return ["+", extra, ["*", factor, expr]]


def _with_stop(curve, stops, at):
    # The value an interpolate takes at zoom `at`, as a stop of its own (outputs may be data
    # expressions, so it's built as one). Outside the stops it holds the nearest one's value
    if at < stops[0][0]:
        return [(at, stops[0][1])] + stops
    if at > stops[-1][0]:
        return stops + [(at, stops[-1][1])]
    for i, ((z0, a), (z1, b)) in enumerate(zip(stops, stops[1:])):
        if z0 < at < z1:
            base = curve[1] if curve[0] == "exponential" else 1
            t = (at - z0) / (z1 - z0) if base == 1 else (base ** (at - z0) - 1) / (base ** (z1 - z0) - 1)
            mid = a + (b - a) * t if all(isinstance(v, (int, float)) for v in (a, b)) else ["+", a, ["*", t, ["-", b, a]]]
            return stops[: i + 1] + [(at, mid)] + stops[i + 1:]
    return stops


def _scale(expr, extra=0):
    # Upstream's width times WIDTH_SCALES, plus extra. ["zoom"] may only feed a top-level
    # interpolate or step, so the outputs are scaled rather than the whole expression wrapped
    if isinstance(expr, list) and expr[0] == "interpolate" and expr[2] == ["zoom"]:
        stops = list(zip(expr[3::2], expr[4::2]))
        for z in WIDTH_SCALES:
            stops = _with_stop(expr[1], stops, z)
        return expr[:3] + [v for z, out in stops for v in (z, _times(out, _width_scale(z), extra))]
    if isinstance(expr, list) and expr[0] == "step" and expr[1] == ["zoom"]:
        rest = expr[3:]
        return expr[:2] + [_times(expr[2], _width_scale(0), extra)] + [
            v for z, out in zip(rest[0::2], rest[1::2]) for v in (z, _times(out, _width_scale(z), extra))]
    return ["step", ["zoom"], _times(expr, _width_scale(0), extra),
            *(v for z in sorted(WIDTH_SCALES) if z > 0 for v in (z, _times(expr, _width_scale(z), extra)))]


def _widen(layer):
    # Upstream lines are thin next to openrailwaymap.org's raster ones: draw them at its widths
    if layer["type"] != "line" or "railway_line" not in layer.get("source-layer", ""):
        return layer
    paint = {**layer["paint"]}
    if "_railing" in layer["id"]:
        # Upstream railings are as thick as the line; keep them a thin stroke hugging the fill.
        # Their line-width matches the fill's, so it sizes the gap
        paint["line-gap-width"] = _scale(paint["line-width"], 2 * HALO_WIDTH)
        paint["line-width"] = 1
        return {**layer, "paint": paint}
    if "_casing" not in layer["id"]:
        paint["line-width"] = _scale(paint["line-width"])
        return {**layer, "paint": paint}
    # Upstream casings stroke both sides of a line-gap-width, which breaks up at sharp vertices and
    # on short low-zoom segments; draw a solid line wider than the fill underneath it instead.
    # Their line-width matches the fill's, so scaling it like the fill keeps the two in step
    paint.pop("line-gap-width", None)
    paint["line-width"] = _scale(paint["line-width"], 2 * HALO_WIDTH)
    # White on any base, as openrailwaymap.org draws it, softened so it fades into the map
    paint.update({"line-color": "white", "line-opacity": 0.7, "line-blur": 1})
    return {**layer, "paint": paint, "layout": {**layer["layout"], "line-join": "round", "line-cap": "round"}}


def tiles_url():
    # Self-hosted Martin (trainlog_orm repo); upstream tiles rate-limit us
    url = load_config().get("openrailwaymap", {}).get("tiles_url") or "https://openrailwaymap.app"
    return url.rstrip("/")


SHARED_LINE_TILES = "/railway_line_high,railway_text_km"


def _absolute_sources(sources):
    # Upstream sources are relative TileJSON paths ("/railway_line_high,railway_text_km");
    # turn them into tile URLs on our host so the browser never asks openrailwaymap.app
    host = tiles_url()
    fixed = {}
    for name, source in sources.items():
        url = source.get("url")
        if isinstance(url, str) and url.startswith("/"):
            # Same tiles as the "high" source (railway_text_km is empty below zoom 10): one URL
            # means one cached, pre-rendered copy (trainlog_orm warm.sh)
            if url == SHARED_LINE_TILES.split(",")[0]:
                url = SHARED_LINE_TILES
            source = {k: v for k, v in source.items() if k != "url"}
            source["tiles"] = [f"{host}{url}/{{z}}/{{x}}/{{y}}"]
            # The zoom ranges the TileJSON gave (Martin's config): past them MapLibre stretches the
            # tiles it has rather than loading new ones, during which lines vanished
            if url == SHARED_LINE_TILES:
                source.update(minzoom=7, maxzoom=14)
            elif url.split(",")[0].endswith("_railway_line_low"):
                source["maxzoom"] = 7
        fixed[name] = source
    return fixed


# Below VECTOR_MINZOOM the overlay is images rendered from render_style on our tile server
# (trainlog_orm, martin-render): upstream's low zoom vector tiles are heavy to decode, and a
# stretched vector tile turns into a staircase while zooming in fast, where an image just blurs
RASTER_MAXZOOM = 5
VECTOR_MINZOOM = 6
OFM_GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf"
LABEL_MINZOOM = 7.5
SKIP_SOURCES = {"dem", "search", "route", "route_stops", "openhistoricalmap"}


def _line_label(layer):
    return (layer["type"] == "symbol" and layer["id"].endswith("_text")
            and layer.get("layout", {}).get("symbol-placement") == "line")


def _drawn(layer):
    # The layers addOrmOverlay adds
    if not layer.get("source") or layer["source"] in SKIP_SOURCES or "_cover" in layer["id"]:
        return False
    if layer["type"] not in ("line", "fill", "circle") and not _line_label(layer):
        return False
    # Dash lengths follow line-width, so a wider dashed casing drifts out of step with its fill
    return "_casing" not in layer["id"] or "line-dasharray" not in layer["paint"]


def render_style(preset):
    style = build_style(preset, raster=False)
    layers = [l for l in style["layers"] if _drawn(l)]
    for layer in layers:
        if _line_label(layer):
            layer["layout"] = {**layer["layout"], "text-font": ["Noto Sans Bold"]}
    used = {l["source"] for l in layers}
    style = {"version": 8, "glyphs": OFM_GLYPHS, "layers": layers,
             "sources": {k: v for k, v in style["sources"].items() if k in used}}
    # MapLibre Native (Martin's renderer) silently drops a layer using what it can't parse:
    # space separated hsl(), as upstream writes its colours, and interpolate-hcl (the speed
    # palette's stops are close enough for a plain blend to look the same)
    text = re.sub(r"hsl\(([\d.]+) ([\d.]+%) ([\d.]+%)\)", r"hsl(\1, \2, \3)", json.dumps(style))
    return json.loads(text.replace('"interpolate-hcl"', '"interpolate"'))


def build_style(preset, raster=True):
    style = _upstream_style()
    state = {k: v["default"] for k, v in style["state"].items()}
    state.update(PRESETS[preset], bearing=0)

    layers = []
    for layer in style["layers"]:
        layer = _drop_hover(_substitute(layer, state))
        visibility = layer.get("layout", {}).get("visibility")
        if isinstance(visibility, list):
            visibility = _evaluate(visibility)
            layer["layout"]["visibility"] = visibility
        # One source for zoom 7 and 8+ (same tiles, SHARED_LINE_TILES): within a source MapLibre
        # keeps drawing the previous zoom's tiles while the next load, across sources it can't
        if layer.get("source") == "openrailwaymap_low":
            layer["source"] = "high"
        # The low zoom lines stay one level past their end, under the zoom 7 lines (the same ones,
        # main lines only), so zooming in across 7 never shows an empty map while those load
        if layer.get("maxzoom") == 7 and layer.get("source", "").endswith("_railway_line_low"):
            layer["maxzoom"] = 8
        # Labels along the lines only close up: zoomed out they crowded the map
        if _line_label(layer):
            layer["minzoom"] = max(layer.get("minzoom", 0), LABEL_MINZOOM)
        if visibility != "none":
            layers.extend(_split_dasharray(_widen(layer)))

    # Upstream interleaves low and zoom 7+ layers (casings, fills, bridges); for the overlap above
    # all low layers go under the first zoom 7+ one, in their own order
    low = [l for l in layers if l.get("source", "").endswith("_railway_line_low")]
    first_high = next((i for i, l in enumerate(layers) if l.get("source") == "high"), len(layers))
    rest = [l for l in layers if l not in low]
    at = sum(1 for l in layers[:first_high] if l not in low)
    layers = rest[:at] + low + rest[at:]
    sources = _absolute_sources(style["sources"])

    if raster:
        for layer in layers:
            if layer.get("source") in sources and layer["source"] not in SKIP_SOURCES:
                layer["minzoom"] = max(layer.get("minzoom", 0), VECTOR_MINZOOM)
        for name, source in sources.items():
            if name.endswith("_railway_line_low"):
                # Or MapLibre would stretch a coarser low zoom tile over the images while loading
                source["minzoom"] = VECTOR_MINZOOM
        # The images end where the vector lines start: stretched under them they doubled every line
        sources["orm-raster"] = {
            "type": "raster", "tileSize": 512, "maxzoom": RASTER_MAXZOOM,
            "tiles": [f"{tiles_url()}/raster/{preset}/{{z}}/{{x}}/{{y}}.png"],
        }
        layers.insert(0, {"id": "orm-raster", "type": "raster", "source": "orm-raster",
                          "maxzoom": VECTOR_MINZOOM, "paint": {"raster-fade-duration": 0}})

    return {**style, "sources": sources, "layers": layers, "state": {}}
