"""Single source of truth for how a trip type is presented.

Before this module the same handful of facts lived in five separate places, with
nothing keeping them in step:

  * ``icon_map`` in app.py (Font Awesome class per type)
  * the ``new_trip_menu`` literal in templates/bootstrap/navigation.html (the same
    classes, written out a second time)
  * ``TRIP_TYPE_GROUPS`` in app.py (order + grouping)
  * ``MapConfig.transportTypes`` in static/js/maplibre-utils.js (class + colour)
  * the ``--<code>`` custom properties in static/styles/style2.css (the same colours)

They had already drifted: `air` is drawn with ``fa-plane`` on the map and
``fa-plane-up`` everywhere else. This module holds it once; Python consumers derive
from it directly.

The two front-end copies (maplibre-utils.js and style2.css) are *not* generated from
this — there is no JS build step, and maplibre-utils.js is loaded by ten templates
including public pages that never see a session. They stay hand-written for now, and
``assert_frontend_in_sync`` exists to catch them drifting apart.

`label` is deliberately absent: labels are translations and live in lang/, keyed by
the same code.
"""

# fmt: off
TRIP_TYPES = {
    # ── Rail ────────────────────────────────────────────────────────────────
    "train":         {"icon": "fa-solid fa-train",             "colour": "#52b0fe", "group": 0, "map": True},
    "tram":          {"icon": "fa-solid fa-train-tram",        "colour": "#a2d7ff", "group": 0, "map": True},
    "metro":         {"icon": "fa-solid fa-train-subway",      "colour": "#004595", "group": 0, "map": True},
    "funicular":     {"icon": "fa-solid fa-mountain",          "colour": "#6495ed", "group": 0, "map": True},
    "rail":          {"icon": "fa-solid fa-dumbbell",          "colour": "#7ec8ff", "group": 0, "map": True},

    # ── Long distance / lifted ──────────────────────────────────────────────
    "air":           {"icon": "fa-solid fa-plane-up",          "colour": "#40b91f", "group": 1, "map": True},
    "bus":           {"icon": "fa-solid fa-bus",               "colour": "#9f4bbb", "group": 1, "map": True},
    "ferry":         {"icon": "fa-solid fa-ship",              "colour": "#1e1e7c", "group": 1, "map": True},
    # No layer of its own: buildTripLayers() rewrites helicopter trips to 'air'
    # before building features, so they ride on the air layer and colour.
    "helicopter":    {"icon": "fa-solid fa-helicopter",        "colour": None,      "group": 1, "map": False},
    "aerialway":     {"icon": "fa-solid fa-cable-car",         "colour": "#afcf3b", "group": 1, "map": True},

    # ── Under your own steam / road ─────────────────────────────────────────
    "walk":          {"icon": "fa-solid fa-person-hiking",     "colour": "#e88c00", "group": 2, "map": True},
    "cycle":         {"icon": "fa-solid fa-bicycle",           "colour": "#6e211a", "group": 2, "map": True},
    "ski":           {"icon": "fa-solid fa-person-skiing",     "colour": "#b8e6f0", "group": 2, "map": True},
    "scooter":       {"icon": "bi bi-scooter",                 "colour": "#00d084", "group": 2, "map": True},
    "car":           {"icon": "fa-solid fa-car-side",          "colour": "#a68fcd", "group": 2, "map": True},
    "other":         {"icon": "fa-solid fa-circle-question",   "colour": "#000000", "group": 2, "map": True},

    # ── Places rather than journeys: single points, so no route to draw ──────
    "accommodation": {"icon": "fa-solid fa-bed",               "colour": "#000000", "group": 3, "map": False},
    "poi":           {"icon": "fa-solid fa-map-location-dot",  "colour": "#000000", "group": 3, "map": False},
    "restaurant":    {"icon": "fa-solid fa-utensils",          "colour": "#000000", "group": 3, "map": False},
}
# fmt: on

# Shown as a fallback for a type the registry does not know (an old row, a hand-edited
# import). Matches what inject_distinct_types used before this module existed.
UNKNOWN_ICON = "fa-solid fa-question"

# ── Derived views ───────────────────────────────────────────────────────────────
# Insertion order above IS the canonical display order, so everything below follows
# from it and cannot disagree with it.

TRIP_TYPE_GROUPS = [
    [t for t, d in TRIP_TYPES.items() if d["group"] == g]
    for g in sorted({d["group"] for d in TRIP_TYPES.values()})
]

TRIP_TYPE_GROUP_INDEX = {t: d["group"] for t, d in TRIP_TYPES.items()}

TRIP_TYPE_SORT_KEY = {t: i for i, t in enumerate(TRIP_TYPES)}

TRIP_TYPE_ICONS = {t: d["icon"] for t, d in TRIP_TYPES.items()}


def order_trip_types(types):
    """Sort trip types into the canonical grouped order; unknown types last."""
    return sorted(types, key=lambda t: TRIP_TYPE_SORT_KEY.get(t, len(TRIP_TYPE_SORT_KEY)))


def icon_for(trip_type):
    """Font Awesome (or Bootstrap Icons) class for a type, with a safe fallback."""
    entry = TRIP_TYPES.get(trip_type)
    return entry["icon"] if entry else UNKNOWN_ICON


def menu_groups():
    """[[(code, icon), ...], ...] in canonical order — what the navbar menu draws.

    Labels are looked up by code in the template, from the splatted lang dict.
    """
    return [[(t, TRIP_TYPES[t]["icon"]) for t in group] for group in TRIP_TYPE_GROUPS]


def assert_frontend_in_sync():
    """Check the two hand-maintained front-end copies still match this registry.

    Not called at import time — it reads files off disk. Run it from a check script
    (or a test, once there is a harness) after touching colours or map icons.
    Returns a list of human-readable problems; empty means they agree.
    """
    import re

    problems = []

    css = open("static/styles/style2.css", encoding="utf-8").read()
    root = re.search(r"^:root\{(.*?)^\}", css, re.S | re.M)
    css_colours = {
        m[0]: "#%02x%02x%02x" % tuple(int(x) for x in m[1].split(","))
        for m in re.findall(r"--([a-z]+)\s*:\s*rgb\(([\d,\s]+)\)", root.group(1))
    }
    for t, d in TRIP_TYPES.items():
        if d["colour"] and css_colours.get(t) and css_colours[t] != d["colour"]:
            problems.append(
                f"style2.css --{t} is {css_colours[t]}, registry says {d['colour']}"
            )

    js = open("static/js/maplibre-utils.js", encoding="utf-8").read()
    for code, icon, colour in re.findall(
        r"\{ id: '([a-z]+)', icon: '([^']+)'.*?color: '(#[0-9a-fA-F]{6})'", js
    ):
        entry = TRIP_TYPES.get(code)
        if not entry:
            problems.append(f"maplibre-utils.js has unknown type '{code}'")
            continue
        if not entry["map"]:
            problems.append(f"maplibre-utils.js draws '{code}' but registry says map=False")
        if colour.lower() != (entry["colour"] or "").lower():
            problems.append(
                f"maplibre-utils.js {code} colour {colour}, registry says {entry['colour']}"
            )
        # The map writes the icon without its style prefix ('fa-train', 'bi-scooter').
        bare = entry["icon"].split()[-1] if entry["icon"].startswith("fa-") else entry["icon"].replace("bi bi-", "bi-")
        if icon != bare:
            problems.append(f"maplibre-utils.js {code} icon '{icon}', registry says '{bare}'")

    drawn = {c for c, _, _ in re.findall(r"\{ id: '([a-z]+)', icon: '([^']+)'.*?color: '(#[0-9a-fA-F]{6})'", js)}
    for t, d in TRIP_TYPES.items():
        if d["map"] and t not in drawn:
            problems.append(f"registry says '{t}' is drawn on the map but maplibre-utils.js has no entry")

    # Every type needs artwork: the trips list, the plan pages and both new-trip forms
    # render images/icons/trip_logos/<code>.png, so a type without one shows a broken
    # or hidden image.
    import os

    logos = "static/images/icons/trip_logos"
    have = {f[:-4] for f in os.listdir(logos) if f.endswith(".png")}
    for t in TRIP_TYPES:
        if t not in have:
            problems.append(f"no artwork at {logos}/{t}.png")
    for extra in sorted(have - set(TRIP_TYPES)):
        problems.append(f"{logos}/{extra}.png matches no trip type (orphan)")

    return problems
