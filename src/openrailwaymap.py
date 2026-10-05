import time

import requests

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
        resp = requests.get("https://openrailwaymap.app/style.json", timeout=10)
        resp.raise_for_status()
        _cache["style"] = resp.json()
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


def build_style(preset):
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
            layers.extend(_split_dasharray(layer))

    return {**style, "layers": layers, "state": {}}
