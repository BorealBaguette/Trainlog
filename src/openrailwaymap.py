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


def _split_dasharray(layer):
    # MapLibre 5.6 has no data-driven line-dasharray: emit one layer per ["match", ["get", "state"], …] branch
    dash = layer.get("paint", {}).get("line-dasharray")
    if not (isinstance(dash, list) and dash[:2] == ["match", ["get", "state"]]):
        return [layer]

    branches = dash[2:-1]
    labels, outputs = branches[0::2], branches[1::2]
    all_labels = [s for label in labels for s in (label if isinstance(label, list) else [label])]
    cases = [(["in", ["get", "state"], ["literal", label if isinstance(label, list) else [label]]], output)
             for label, output in zip(labels, outputs)]
    cases.append((["!", ["in", ["get", "state"], ["literal", all_labels]]], dash[-1]))

    split = []
    for i, (condition, output) in enumerate(cases):
        filter_ = ["all", layer["filter"], condition] if "filter" in layer else condition
        split.append({**layer, "id": f"{layer['id']}_{i}", "filter": filter_,
                      "paint": {**layer["paint"], "line-dasharray": output}})
    return split


LINE_WIDTH_SCALE = 2
HALO_WIDTH = 1


def _scale(expr, scale, extra=0):
    if isinstance(expr, (int, float)):
        return expr * scale + extra
    # ["zoom"] may only feed a top-level interpolate/step, so scale its outputs rather than wrap it
    if expr[0] == "interpolate":
        return expr[:3] + [_scale(e, scale, extra) if i % 2 else e for i, e in enumerate(expr[3:])]
    if expr[0] == "step":
        return expr[:2] + [e if i % 2 else _scale(e, scale, extra) for i, e in enumerate(expr[2:])]
    return ["+", extra, ["*", scale, expr]]


def _widen(layer, bold):
    if layer["type"] != "line" or "railway_line" not in layer.get("source-layer", ""):
        return layer
    scale = LINE_WIDTH_SCALE if bold else 1
    paint = {**layer["paint"]}
    if "_railing" in layer["id"]:
        # Upstream railings are as thick as the line; keep them a thin stroke hugging the fill.
        # Their line-width matches the fill's, so it sizes the gap
        paint["line-gap-width"] = _scale(paint["line-width"], scale, 2 * HALO_WIDTH)
        paint["line-width"] = 1
        return {**layer, "paint": paint}
    # Upstream lines are thin next to the old raster tiles; make them stand out over busy raster bases
    if not bold:
        return layer
    if "_casing" not in layer["id"]:
        paint["line-width"] = _scale(paint["line-width"], scale)
        return {**layer, "paint": paint}
    # Upstream casings stroke both sides of a line-gap-width, which breaks up at sharp vertices and
    # on short low-zoom segments; draw a solid line wider than the fill underneath it instead.
    # Their line-width matches the fill's, so scaling it like the fill keeps the two in step
    paint.pop("line-gap-width", None)
    paint["line-width"] = _scale(paint["line-width"], scale, 2 * HALO_WIDTH)
    layout = {**layer["layout"], "line-join": "round"}
    if "line-dasharray" not in paint:
        layout["line-cap"] = "round"
    return {**layer, "paint": paint, "layout": layout}


def tiles_url():
    # Self-hosted Martin (trainlog_orm repo); upstream tiles rate-limit us
    url = load_config().get("openrailwaymap", {}).get("tiles_url") or "https://openrailwaymap.app"
    return url.rstrip("/")


def _absolute_sources(sources):
    # Upstream sources are relative TileJSON paths ("/railway_line_high,railway_text_km");
    # turn them into tile URLs on our host so the browser never asks openrailwaymap.app
    host = tiles_url()
    fixed = {}
    for name, source in sources.items():
        url = source.get("url")
        if isinstance(url, str) and url.startswith("/"):
            source = {k: v for k, v in source.items() if k != "url"}
            source["tiles"] = [f"{host}{url}/{{z}}/{{x}}/{{y}}"]
        fixed[name] = source
    return fixed


def build_style(preset, bold=False):
    style = _upstream_style()
    state = {k: v["default"] for k, v in style["state"].items()}
    state.update(PRESETS[preset], bearing=0)

    layers = []
    for layer in style["layers"]:
        layer = _substitute(layer, state)
        visibility = layer.get("layout", {}).get("visibility")
        if isinstance(visibility, list):
            visibility = _evaluate(visibility)
            layer["layout"]["visibility"] = visibility
        if visibility != "none":
            layers.extend(_split_dasharray(_widen(layer, bold)))

    return {**style, "sources": _absolute_sources(style["sources"]), "layers": layers, "state": {}}
