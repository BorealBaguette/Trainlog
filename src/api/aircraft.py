"""Aircraft types page: every aircraft family Trainlog knows, which of them
the user has flown on, and each family's variants underneath."""

import os
import re

from flask import Blueprint, render_template, session

from src.aircraft_families import ALIASES, FAMILY_OF, GENERICS, SORT_AS
from src.pg import pg_session
from src.sql import aircraft as aircraft_sql
from src.utils import get_user_id, getUser, lang, public_required

aircraft_blueprint = Blueprint("aircraft", __name__)

AIRLINER_DIR = os.path.join("static", "images", "airliners")


def _summarise(trips):
    """Flights, km, first and last date from {trip_id: (length_m, date)}."""
    dates = [d for _, d in trips.values() if d]
    return {
        "flights": len(trips),
        "distance_km": round(sum(length or 0 for length, _ in trips.values()) / 1000),
        "first_flown": min(dates).isoformat() if dates else None,
        "last_flown": max(dates).isoformat() if dates else None,
    }


# Airbus and Boeing lead the page; everyone else follows alphabetically.
MANUFACTURER_FIRST = {"airbus": 0, "boeing": 1}


def _natural(text):
    """Sort key that orders "F50" before "F100" and "737-600" before "737-900"."""
    return [
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"(\d+)", text.casefold())
        if part
    ]


def _sort_key(manufacturer, name):
    rank = MANUFACTURER_FIRST.get((manufacturer or "").strip().casefold(), 2)
    return (rank, _natural(name))


def _image(code):
    """Static path of a type's picture. Admins can add a type before its
    picture is uploaded; it still shows, just without an outline."""
    if os.path.isfile(os.path.join(AIRLINER_DIR, f"{code}.png")):
        return f"images/airliners/{code}.png"
    return None


def get_aircraft_families(user_id):
    with pg_session() as pg:
        rows = pg.execute(
            aircraft_sql.aircraft_types(), {"user_id": user_id}
        ).fetchall()

    types = {}
    for row in rows:
        r = row._mapping
        types[r["code"]] = {
            "manufacturer": r["manufacturer"],
            "name": " ".join(p for p in (r["manufacturer"], r["model"]) if p)
            or r["code"],
            "trips": {
                trip_id: (length, flown_on)
                for trip_id, length, flown_on in zip(
                    r["trip_ids"], r["trip_lengths"], r["flown_on"]
                )
            },
        }

    # An alias's flights belong to the type it stands for, and it is not
    # listed on its own (unless that type is missing from the table).
    for alias, target in ALIASES.items():
        if alias in types and target in types:
            types[target]["trips"].update(types.pop(alias)["trips"])

    # A generic code stands for its whole family: kept aside so it is not a
    # variant, then used for the family's picture and flights. If no variant
    # of that family is in the table, it is listed as a plain type instead.
    generics = {}
    for code, family_name in GENERICS.items():
        has_variant = any(
            FAMILY_OF.get(other, (None,))[0] == family_name for other in types
        )
        if code in types and has_variant:
            generics[family_name] = {"code": code, **types.pop(code)}

    families = {}
    for code, t in types.items():
        family_name, representative = FAMILY_OF.get(code, (t["name"], 0))
        family = families.setdefault(
            family_name,
            {
                "name": family_name,
                "manufacturer": t["manufacturer"],
                "variants": [],
                "trips": {},
            },
        )
        family["trips"].update(t["trips"])
        family["variants"].append(
            {
                "code": code,
                "name": t["name"],
                "representative": representative,
                "image": _image(code),
                **_summarise(t["trips"]),
            }
        )

    for family_name, generic in generics.items():
        families[family_name]["trips"].update(generic["trips"])

    result = []
    for family in families.values():
        generic = generics.get(family["name"])
        variants = sorted(family["variants"], key=lambda v: _sort_key(None, v["name"]))
        flown = [v for v in variants if v["flights"]]
        # The family is pictured by its generic code if it has one, else by the
        # variant flown most, else by its usual representative (listed first
        # in AIRCRAFT_FAMILIES).
        if flown:
            pictured = max(flown, key=lambda v: v["flights"])
        else:
            pictured = min(variants, key=lambda v: v["representative"])
        image = (generic and _image(generic["code"])) or pictured["image"]
        result.append(
            {
                "name": family["name"],
                "manufacturer": family["manufacturer"],
                "image": image
                or next((v["image"] for v in variants if v["image"]), None),
                "variants": variants,
                "variants_flown": len(flown),
                **_summarise(family["trips"]),
            }
        )

    result.sort(
        key=lambda f: _sort_key(f["manufacturer"], SORT_AS.get(f["name"], f["name"]))
    )
    return result


@aircraft_blueprint.route("/u/<username>/aircraft")
@public_required
def aircraft_types(username):
    families = get_aircraft_families(get_user_id(username))
    user_lang = lang[session["userinfo"]["lang"]]
    return render_template(
        "aircraft_types.html",
        title=user_lang["aircraftTypesTitle"],
        username=username,
        families=families,
        flown_count=sum(1 for f in families if f["flights"]),
        nav="bootstrap/navigation.html"
        if username == getUser()
        else "bootstrap/public_nav.html",
        **user_lang,
        **session["userinfo"],
    )
