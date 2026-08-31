"""
Ranking of the people who drew the wagon catalogue.

MLG Traffic is the whole ~18k base and everyone else adds stock one wagon at a
time, so MLG is served as a headline and the ranking covers the rest.
"""

import json
import re
from collections import Counter
from urllib.parse import urlparse

from flask import Blueprint, jsonify

from src.api.admin.wagons import _wagon_usage
from src.api.trainset import _slim_units
from src.pg import pg_session

wagon_leaderboard_blueprint = Blueprint("wagon_leaderboard", __name__)

MLG_SOURCE = "MLG Traffic"
MLG_URL = "https://mlgtraffic.net"
TRAINLOG_AUTHOR = "https://trainlog.me/public/"

# Trainsets shown per artist: one strip is drawn on load, the rest open on click.
SETS_PER_ARTIST = 4
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


def _representatives(wagons, public_sets, catalogue):
    """The public trainsets showing off most of `wagons`, best first."""
    scored = []
    for name, units in public_sets:
        hits = sum(1 for u in units if u.get("name") in wagons)
        if hits:
            scored.append((hits, name, units))
    scored.sort(key=lambda s: (-s[0], s[1].lower()))
    return [{"name": name, "units": _enrich(_slim_units(units), catalogue)}
            for _, name, units in scored[:SETS_PER_ARTIST]]


def wagon_authors():
    trips, riders, trainsets, wagon_countries = _wagon_usage()

    with pg_session() as pg:
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

        ranked = sorted(authors.values(), key=lambda a: (-a["drawings"], a["name"].lower()))

        public_sets = []
        for row in pg.execute(
            "SELECT name, units_json FROM trainsets WHERE is_admin"
        ).fetchall():
            try:
                public_sets.append((row["name"], json.loads(row["units_json"] or "[]")))
            except ValueError:
                continue

        # One lookup for every wagon those sets can draw, rather than a query per
        # unit per set per artist.
        set_wagons = sorted({u.get("name") for _, units in public_sets for u in units
                             if isinstance(u, dict) and u.get("name")})
        catalogue = {
            r["name"]: dict(r)
            for r in pg.execute(
                f"SELECT {_UNIT_COLS} FROM wagons WHERE name = ANY(:names)",
                {"names": set_wagons},
            ).fetchall()
        }

        for entry in [headline, *ranked]:
            entry["trainsets_shown"] = _representatives(
                entry.pop("wagons"), public_sets, catalogue
            )
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
