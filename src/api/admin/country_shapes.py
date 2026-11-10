import os

from flask import jsonify, render_template, request, send_file, session

from py import geopip_perso
from src.users import User
from src.utils import getUser, has_current_trip, lang

GEOJSON = "static/data/countries-filtered.geojson"


def country_shapes_page():
    user = User.query.filter_by(username=getUser()).first()
    return render_template(
        "admin/country_shapes.html",
        title="Country shapes",
        nav="bootstrap/navigation.html",
        username=getUser(),
        tileserver=user.tileserver if user else "default",
        isCurrent=has_current_trip(),
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


def country_shapes_json():
    """The country lookup's shapes, unsimplified: the page is for spotting their flaws."""
    return send_file(os.path.abspath(GEOJSON), mimetype="application/geo+json")


def country_shapes_at():
    """Every shape the country lookup finds at ?lat=&lng=, as it loaded them at startup."""
    lat, lng = float(request.args["lat"]), float(request.args["lng"])
    return jsonify([
        {"countryCode": p["countryCode"], "id_2": p.get("id_2")}
        for p in geopip_perso.search_all(lng=lng, lat=lat)
    ])
