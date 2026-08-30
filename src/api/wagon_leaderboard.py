"""
Ranking of the people who drew the wagon catalogue.

MLG Traffic is the whole ~18k base and everyone else adds stock one wagon at a
time, so MLG is served as a headline and the ranking covers the rest.
"""

import json
import re
from urllib.parse import urlparse

from flask import Blueprint, jsonify

from src.api.admin.wagons import _wagon_usage
from src.api.trainset import _enrich_units, _slim_units
from src.pg import pg_session

wagon_leaderboard_blueprint = Blueprint("wagon_leaderboard", __name__)

MLG_SOURCE = "MLG Traffic"
MLG_URL = "https://mlgtraffic.net"
TRAINLOG_AUTHOR = "https://trainlog.me/public/"


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


def _representative(pg, wagons, public_sets):
    """The public trainset showing off most of `wagons`, rendered as enriched units."""
    best_name, best_units, best_hits = None, [], 0
    for name, units in public_sets:
        hits = sum(1 for u in units if u.get("name") in wagons)
        if hits > best_hits:
            best_name, best_units, best_hits = name, units, hits
    if not best_hits:
        return None
    return {"name": best_name, "units": _enrich_units(pg, _slim_units(best_units))}


def wagon_authors():
    trips, riders, trainsets = _wagon_usage()

    with pg_session() as pg:
        rows = pg.execute(
            "SELECT name, author, license, source FROM wagons WHERE image IS NOT NULL"
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
                if row["license"]:
                    entry["licenses"].add(row["license"])

        ranked = sorted(authors.values(), key=lambda a: (-a["drawings"], a["name"].lower()))

        public_sets = []
        for row in pg.execute(
            "SELECT name, units_json FROM trainsets WHERE is_admin"
        ).fetchall():
            try:
                public_sets.append((row["name"], json.loads(row["units_json"] or "[]")))
            except ValueError:
                continue

        for entry in [headline, *ranked]:
            entry["trainset"] = _representative(pg, entry.pop("wagons"), public_sets)
            entry["users"] = len(entry["users"])
            entry["licenses"] = sorted(entry["licenses"])

    return {"headline": headline, "authors": ranked}


def _new_entry(credit):
    return {**credit, "drawings": 0, "trips": 0, "trainsets": 0,
            "users": set(), "licenses": set(), "wagons": set()}


@wagon_leaderboard_blueprint.route("/getWagonAuthors")
def get_wagon_authors():
    return jsonify(wagon_authors())
