"""
Ranking of the people who drew the wagon catalogue.

MLG Traffic is the whole ~18k base and everyone else adds stock one wagon at a
time, so MLG is served as a headline and the ranking covers the rest.
"""

import json
import re
from collections import Counter
from urllib.parse import urlparse

from flask import Blueprint, abort, jsonify, render_template, request, session

from src.api.admin.trainsets import _trainset_usage
from src.api.admin.wagons import _wagon_usage
from src.api.trainset import _slim_units
from src.pg import pg_session
from src.utils import getUser, lang

wagon_leaderboard_blueprint = Blueprint("wagon_leaderboard", __name__)

MLG_SOURCE = "MLG Traffic"
# HTTP only — the host resets TLS connections (see also mlg_crawl.py).
MLG_URL = "http://mlgtraffic.net"
TRAINLOG_AUTHOR = "https://trainlog.me/public/"

# Personal sites whose owner signs by name rather than by URL. Keyed by host, so
# http/https and a www. prefix all land on the same person. Mirrored by
# SITE_AUTHORS in templates/includes/trainset_display.html, which credits the same
# people under the strips.
SITE_AUTHORS = {
    "arthurstreinenpagina.nl": "Arthur Pijpers",
}

# Trainsets shown per artist: one strip is drawn on load, the rest open on click.
SETS_PER_ARTIST = 4
# Drawings shown when no public trainset uses any of them — trams and other single
# units are logged on their own, so their artists would otherwise get a bare card.
SOLO_WAGONS = 5
# Flags shown per artist, most-travelled first.
FLAGS_PER_ARTIST = 6

# What the strip renderer needs — deliberately leaner than the trainset editor's
# enrichment, since a page carrying several sets for every artist would otherwise
# ship a lot of unused prose (notes, era, categories).
_UNIT_COLS = ("name, label, image, image_type, image_ext, px_per_meter, "
              "author, license, source")


def _credit(author):
    author = author.strip().strip("/")
    if not author:
        return None
    if author.startswith(TRAINLOG_AUTHOR):
        username = author[len(TRAINLOG_AUTHOR):].strip("/")
        return {"name": username, "url": f"/public/{username}", "username": username}
    if author.startswith("http"):
        host = urlparse(author).netloc.lower().removeprefix("www.")
        if host in SITE_AUTHORS:
            return {"name": SITE_AUTHORS[host], "url": author, "username": None}
        match = re.search(r"User:([^/]+)", author)
        name = match.group(1) if match else urlparse(author).netloc
        # Commons appends the home wiki to imported accounts; nobody signs that way
        name = re.sub(r"~[a-z]+wiki$", "", name)
        return {"name": name, "url": author, "username": None}
    return {"name": author, "url": None, "username": None}


def _enrich(units, catalogue):
    """Slim unit refs → display units, from a pre-loaded wagons lookup."""
    enriched = []
    for u in units:
        wagon = catalogue.get(u["name"])
        unit = dict(wagon) if wagon else {
            "name": u["name"], "label": u.get("label", u["name"]),
            "image": None, "image_type": "plain", "image_ext": "gif",
            "px_per_meter": None, "source": None, "author": None, "license": None,
        }
        unit["_side"] = u.get("_side", "L")
        if u.get("_phType"):
            unit["_phType"] = u["_phType"]
        enriched.append(unit)
    return enriched


def _representatives(wagons, public_sets, wagon_trips, ts_trips):
    """
    What to draw for an artist, as (set name or None, slim units), best first.

    Normally the public trainsets that use this artist's stock, most-travelled
    first, with a set built entirely from their wagons preferred as the showcase.
    Where no public set uses any of them — a tram or a railcar runs as itself, so
    nobody builds a composition for it — fall back to a strip of the artist's own
    busiest drawings, unnamed, since it is a sampler rather than a real train.
    """
    scored = []
    for tid, name, units in public_sets:
        named = [u for u in units if u.get("name")]
        hits = sum(1 for u in named if u["name"] in wagons)
        if hits:
            all_mine = bool(named) and hits == len(named)
            scored.append((all_mine, ts_trips.get(tid, 0), name, units))
    if scored:
        scored.sort(key=lambda s: (-s[0], -s[1], s[2].lower()))
        return [(name, _slim_units(units)) for _, _, name, units in scored[:SETS_PER_ARTIST]]

    solo = sorted(wagons, key=lambda n: (-wagon_trips.get(n, 0), n))[:SOLO_WAGONS]
    return [(None, [{"name": n} for n in solo])] if solo else []


def _collect_authors(pg):
    """(headline entry, {name: entry}), each still carrying its `wagons` name set.

    Shared by the ranking and the per-artist page — the ranking pops `wagons` once
    it has chosen representatives, the artist page needs the whole set.
    """
    trips, riders, trainsets, wagon_countries = _wagon_usage()
    rows = pg.execute(
        "SELECT name, author, source FROM wagons WHERE image IS NOT NULL"
    ).fetchall()

    headline = _new_entry({"name": MLG_SOURCE, "url": MLG_URL, "username": None})
    authors = {}
    for row in rows:
        if row["source"] == MLG_SOURCE:
            credits = [headline]
        else:
            credits = []
            for part in (row["author"] or "").split(","):
                credit = _credit(part)
                if credit:
                    credits.append(authors.setdefault(credit["name"], _new_entry(credit)))
        for entry in credits:
            entry["drawings"] += 1
            entry["trips"] += trips.get(row["name"], 0)
            entry["trainsets"] += trainsets.get(row["name"], 0)
            entry["users"] |= riders.get(row["name"], set())
            entry["wagons"].add(row["name"])
            entry["countries"].update(wagon_countries.get(row["name"], {}))
    return headline, authors


def _public_sets(pg):
    """(id, name, units) for every curated trainset, malformed rows skipped."""
    sets = []
    for row in pg.execute(
        "SELECT id, name, units_json FROM trainsets WHERE is_admin"
    ).fetchall():
        try:
            sets.append((row["id"], row["name"], json.loads(row["units_json"] or "[]")))
        except ValueError:
            continue
    return sets


def _catalogue_for(pg, slim_sets):
    """One wagons lookup covering every unit drawn across `slim_sets`."""
    needed = sorted({u["name"] for units in slim_sets for u in units})
    return {
        r["name"]: dict(r)
        for r in pg.execute(
            f"SELECT {_UNIT_COLS} FROM wagons WHERE name = ANY(:names)",
            {"names": needed},
        ).fetchall()
    }


def wagon_authors():
    trips, *_ = _wagon_usage()
    ts_trips, _ = _trainset_usage()

    with pg_session() as pg:
        headline, authors = _collect_authors(pg)
        ranked = sorted(authors.values(), key=lambda a: (-a["drawings"], a["name"].lower()))
        public_sets = _public_sets(pg)

        entries = [headline, *ranked]
        chosen = [_representatives(entry.pop("wagons"), public_sets, trips, ts_trips)
                  for entry in entries]

        # One lookup for every wagon actually drawn, rather than a query per unit
        # per set per artist.
        catalogue = _catalogue_for(pg, [units for sets in chosen for _, units in sets])

        for entry, sets in zip(entries, chosen):
            entry["trainsets_shown"] = [
                {"name": name, "units": _enrich(units, catalogue)}
                for name, units in sets
            ]
            entry["users"] = len(entry["users"])
            entry["countries"] = [cc for cc, _ in
                                  entry["countries"].most_common(FLAGS_PER_ARTIST)]

    return {"headline": headline, "authors": ranked}


def _new_entry(credit):
    return {**credit, "drawings": 0, "trips": 0, "trainsets": 0,
            "users": set(), "countries": Counter(), "wagons": set()}


@wagon_leaderboard_blueprint.route("/getWagonAuthors")
def get_wagon_authors():
    return jsonify(wagon_authors())


def wagon_artist(key):
    """Everything one artist drew: all their wagons, and every curated trainset that
    uses at least one, both ordered most-travelled first, that artist's cars flagged
    for the strip renderer.

    Returns None when `key` (a credit name from the ranking) matches nobody. MLG is
    the whole ~18k base — served as an external headline, never as an on-site page.
    """
    trips, _riders, set_counts, _countries = _wagon_usage()
    ts_trips, _ = _trainset_usage()

    with pg_session() as pg:
        _, authors = _collect_authors(pg)
        entry = authors.get(key)
        if entry is None:
            return None
        mine = entry["wagons"]

        wagons = []
        for r in pg.execute(
            f"SELECT {_UNIT_COLS} FROM wagons WHERE name = ANY(:names)",
            {"names": sorted(mine)},
        ).fetchall():
            w = dict(r)
            w["trips"] = trips.get(w["name"], 0)
            w["trainsets"] = set_counts.get(w["name"], 0)
            wagons.append(w)
        wagons.sort(key=lambda w: (-w["trips"], -w["trainsets"],
                                   (w["label"] or w["name"]).lower()))

        scored = []
        for tid, name, units in _public_sets(pg):
            if any(u.get("name") in mine for u in units):
                scored.append((ts_trips.get(tid, 0), name, _slim_units(units)))
        scored.sort(key=lambda s: (-s[0], s[1].lower()))

        catalogue = _catalogue_for(pg, [units for _, _, units in scored])
        trainsets = [
            {"name": name, "trips": trip_n, "units": _enrich(units, catalogue)}
            for trip_n, name, units in scored
        ]

    return {
        "name": entry["name"],
        "url": entry["url"],
        "username": entry["username"],
        "wagons": wagons,
        "trainsets": trainsets,
    }


@wagon_leaderboard_blueprint.route("/leaderboard/wagons/artist")
def wagon_artist_page():
    detail = wagon_artist(request.args.get("name", "").strip())
    if detail is None:
        abort(404)
    strings = lang[session["userinfo"]["lang"]]
    return render_template(
        "wagon_artist.html",
        nav="bootstrap/no_user_nav.html" if getUser() == "public"
        else "bootstrap/navigation.html",
        username=getUser(),
        detail=detail,
        title=strings["leaderboardWagons"],
        **strings,
        **session["userinfo"],
    )
