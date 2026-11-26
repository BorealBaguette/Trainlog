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


def _load_types(pg):
    """Every row of `airliners`, by upper-case code (trips are matched that way)."""
    return {
        row["iata"].strip().upper(): {
            "code": row["iata"],
            "manufacturer": row["manufacturer"],
            "name": " ".join(p for p in (row["manufacturer"], row["model"]) if p)
            or row["iata"],
        }
        for row in pg.execute(
            "SELECT iata, manufacturer, model FROM airliners WHERE iata IS NOT NULL"
        ).fetchall()
    }


def resolve_codes(types):
    """What each type code counts as: {code: (variant code or None, family)}.

    - Most codes are a variant of their own.
    - An alias (a code clashing with a specific model) counts as that model.
    - A generic code stands for its whole family: no variant (None), but its
      flights still count as flying the family. If no variant of that family
      is in the table it is treated as a plain variant instead.

    The aircraft page and the leaderboard both go through this, so they
    always agree on what a flight is worth.
    """

    def family_of(code):
        return FAMILY_OF.get(code, (types[code]["name"],))[0]

    families_with_variants = {
        family_of(code)
        for code in types
        if code not in GENERICS and not (code in ALIASES and ALIASES[code] in types)
    }
    resolved = {}
    for code in types:
        target = ALIASES.get(code)
        if target in types:
            resolved[code] = (target, family_of(target))
        elif code in GENERICS and GENERICS[code] in families_with_variants:
            resolved[code] = (None, GENERICS[code])
        else:
            resolved[code] = (code, family_of(code))
    return resolved


def get_aircraft_families(user_id):
    with pg_session() as pg:
        types = _load_types(pg)
        flown = pg.execute(
            aircraft_sql.flown_aircraft(codes_only=False), {"user_ids": [user_id]}
        ).fetchall()
    resolved = resolve_codes(types)

    families = {}
    for code, (variant, family_name) in resolved.items():
        family = families.setdefault(
            family_name,
            {
                "name": family_name,
                "manufacturer": types[code]["manufacturer"],
                "variants": {},
                "generic": None,
                "trips": {},
            },
        )
        if variant is None:
            family["generic"] = types[code]["code"]
        elif variant == code:
            family["variants"][code] = {
                "code": types[code]["code"],
                "name": types[code]["name"],
                "representative": FAMILY_OF.get(code, (None, 0))[1],
                "image": _image(types[code]["code"]),
                "trips": {},
            }

    # Codes outside the table have no picture or family, so are left out.
    for row in flown:
        if row["code"] not in resolved:
            continue
        variant, family_name = resolved[row["code"]]
        trip = (row["trip_length"], row["flown_on"])
        family = families[family_name]
        family["trips"][row["trip_id"]] = trip
        if variant is not None:
            family["variants"][variant]["trips"][row["trip_id"]] = trip

    result = []
    for family in families.values():
        variants = sorted(
            ({**v, **_summarise(v.pop("trips"))} for v in family["variants"].values()),
            key=lambda v: _sort_key(None, v["name"]),
        )
        flown_variants = [v for v in variants if v["flights"]]
        # The family is pictured by its generic code if it has one, else by the
        # variant flown most, else by its usual representative (listed first
        # in AIRCRAFT_FAMILIES).
        if flown_variants:
            pictured = max(flown_variants, key=lambda v: v["flights"])
        else:
            pictured = min(variants, key=lambda v: v["representative"])
        image = (family["generic"] and _image(family["generic"])) or pictured["image"]
        result.append(
            {
                "name": family["name"],
                "manufacturer": family["manufacturer"],
                "image": image
                or next((v["image"] for v in variants if v["image"]), None),
                "variants": variants,
                "variants_flown": len(flown_variants),
                **_summarise(family["trips"]),
            }
        )

    result.sort(
        key=lambda f: _sort_key(f["manufacturer"], SORT_AS.get(f["name"], f["name"]))
    )
    return result


def aircraft_leaderboard(user_ids):
    """{user_id: {"variants": n, "families": n}} for users who have flown any
    type in the table. A variant scores once however often it was flown; an
    alias scores as the model it stands for, a generic code only as its family.
    """
    with pg_session() as pg:
        types = _load_types(pg)
        rows = pg.execute(
            aircraft_sql.flown_aircraft(codes_only=True), {"user_ids": user_ids}
        ).fetchall()
    resolved = resolve_codes(types)

    flown = {}
    for row in rows:
        if row["code"] not in resolved:
            continue
        variant, family_name = resolved[row["code"]]
        user = flown.setdefault(row["user_id"], {"variants": set(), "families": set()})
        user["families"].add(family_name)
        if variant is not None:
            user["variants"].add(variant)
    return {
        uid: {"variants": len(u["variants"]), "families": len(u["families"])}
        for uid, u in flown.items()
    }


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
        variants_total=sum(len(f["variants"]) for f in families),
        variants_flown=sum(f["variants_flown"] for f in families),
        nav="bootstrap/navigation.html"
        if username == getUser()
        else "bootstrap/public_nav.html",
        **user_lang,
        **session["userinfo"],
    )
