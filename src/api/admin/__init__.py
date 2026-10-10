import json
import logging
import re

from flask import Blueprint, jsonify, render_template, request, session

from py.utils import get_all_countries, get_flag_emoji
from src.pg import pg_session
from src.api.station_explorer import explorer_page
from src.quai import forget_station_merges, quai_station, station_merges
from src.suspicious_activity import list_denied_logins, list_suspicious_activity
from src.utils import admin_required, getUser, has_current_trip, lang, owner_required

from .operators import operators_api_blueprint
from .trainsets import trainsets_admin_blueprint
from .wagons import wagons_admin_blueprint

logger = logging.getLogger(__name__)

admin_blueprint = Blueprint("admin", __name__)


@admin_blueprint.route("/denied_logins")
@owner_required
def denied_logins():
    denied_logins = list_denied_logins()
    denied_logins = [dict(login) for login in denied_logins]

    for login in denied_logins:
        login["ip_emoji"] = get_flag_emoji(login["ip_country"])

    return render_template(
        "admin/denied_logins.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        denied_logins=denied_logins,
        isCurrent=has_current_trip(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@admin_blueprint.route("/suspicious")
@owner_required
def suspicious_activity():
    limit = request.args.get("limit", "2000")
    if limit == "all":
        limit = None
    else:
        try:
            limit = int(limit)
        except ValueError:
            limit = 2000

    suspicious_activities_base = list_suspicious_activity(limit)
    suspicious_activities = []

    for activity in suspicious_activities_base:
        suspicious_activities.append(dict(activity))
        suspicious_activities[-1]["ip_emoji"] = get_flag_emoji(
            suspicious_activities[-1]["ip_country"]
        )

    return render_template(
        "admin/suspicious_activity.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        activities=suspicious_activities,
        isCurrent=has_current_trip(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@admin_blueprint.route("/wagons")
@admin_required
def wagons_admin():
    return render_template(
        "admin/wagons.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        country_list=get_all_countries(),
        isCurrent=has_current_trip(),
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


@admin_blueprint.route("/trainsets")
@admin_required
def trainsets_admin():
    return render_template(
        "admin/trainsets.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        isCurrent=has_current_trip(),
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


@admin_blueprint.route("/station_explorer")
@admin_required
def station_explorer():
    return explorer_page(editable=True)


@admin_blueprint.route("/station_explorer/merge/<mode>/<key>", methods=["POST"])
@admin_required
def station_explorer_merge(mode, key):
    """Merges a quai station into another of its mode ({into: station_key}), which Trainlog
    then takes it as everywhere (src/quai.py, apply_overrides); {into: null} undoes it."""
    into = ((request.get_json(silent=True) or {}).get("into") or "").strip() or None
    if into == key:
        return jsonify(error="a station cannot be merged into itself"), 400
    if into:
        if not quai_station(mode, into):
            return jsonify(error="unknown station"), 404
        # One step only: not into a station itself merged, nor one others are merged into.
        merges = station_merges()
        if (mode, into) in merges:
            return jsonify(error="that station is itself merged into another"), 400
        if any(m == mode and t == key for (m, _), t in merges.items()):
            return jsonify(error="other stations are merged into this one: undo those first"), 400
    with pg_session() as pg:
        pg.execute(
            """
            INSERT INTO station_overrides (mode, station_key, merged_into, updated_by)
            VALUES (:mode, :key, :into, :user)
            ON CONFLICT (mode, station_key) DO UPDATE SET
                merged_into = EXCLUDED.merged_into, updated_by = EXCLUDED.updated_by,
                updated_on = now()
            """,
            {"mode": mode, "key": key, "into": into, "user": getUser()},
        )
        pg.execute(
            """
            DELETE FROM station_overrides WHERE mode = :mode AND station_key = :key
              AND name IS NULL AND lat IS NULL AND tracks IS NULL AND names IS NULL
              AND merged_into IS NULL
            """,
            {"mode": mode, "key": key},
        )
    forget_station_merges()
    return jsonify(ok=True)


@admin_blueprint.route("/station_explorer/override/<mode>/<key>", methods=["POST"])
@admin_required
def station_explorer_override(mode, key):
    """Sets the name, position, tracks and names in given languages Trainlog uses for a quai
    station ({name, lat, lng, tracks, names}, any of them empty; tracks as merge_tracks takes
    them, names as {language: name}); with all of them empty, removes the override."""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip() or None
    try:
        lat = float(body["lat"]) if body.get("lat") not in (None, "") else None
        lng = float(body["lng"]) if body.get("lng") not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify(error="lat and lng must be numbers"), 400
    if (lat is None) != (lng is None) or (lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180)):
        return jsonify(error="give both lat and lng, or neither"), 400
    tracks = []
    for t in body.get("tracks") or []:
        ref = str((t or {}).get("ref") or "").strip()
        if not ref or len(ref) > 10:
            return jsonify(error="each track needs a ref of at most 10 characters"), 400
        if t.get("hidden"):
            tracks.append({"ref": ref, "hidden": True})
            continue
        try:
            t_lat, t_lng = float(t["lat"]), float(t["lng"])
        except (KeyError, TypeError, ValueError):
            return jsonify(error=f"track {ref} needs a position"), 400
        if not (-90 <= t_lat <= 90 and -180 <= t_lng <= 180):
            return jsonify(error=f"track {ref}: position out of range"), 400
        tracks.append({"ref": ref, "lat": t_lat, "lng": t_lng, "on_track": bool(t.get("on_track"))})
    names = {}
    for code, value in (body.get("names") or {}).items():
        code, value = str(code).strip(), str(value or "").strip()
        if not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", code):
            return jsonify(error=f"{code!r} is not a language code (ja, pt-BR)"), 400
        if not value or len(value) > 200:
            return jsonify(error=f"the {code} name must be 1 to 200 characters"), 400
        names[code] = value
    with pg_session() as pg:
        if name is None and lat is None and not tracks and not names:
            # Cleared: gone, unless it still says what the station is merged into.
            pg.execute(
                """
                UPDATE station_overrides SET name = NULL, lat = NULL, lng = NULL, tracks = NULL,
                    names = NULL
                WHERE mode = :mode AND station_key = :key
                """,
                {"mode": mode, "key": key},
            )
            pg.execute(
                """
                DELETE FROM station_overrides WHERE mode = :mode AND station_key = :key
                  AND merged_into IS NULL
                """,
                {"mode": mode, "key": key},
            )
        else:
            pg.execute(
                """
                INSERT INTO station_overrides (mode, station_key, name, lat, lng, tracks, names,
                                               updated_by)
                VALUES (:mode, :key, :name, :lat, :lng, CAST(:tracks AS jsonb),
                        CAST(:names AS jsonb), :user)
                ON CONFLICT (mode, station_key) DO UPDATE SET
                    name = EXCLUDED.name, lat = EXCLUDED.lat, lng = EXCLUDED.lng,
                    tracks = EXCLUDED.tracks, names = EXCLUDED.names,
                    updated_by = EXCLUDED.updated_by, updated_on = now()
                """,
                {"mode": mode, "key": key, "name": name, "lat": lat, "lng": lng,
                 "tracks": json.dumps(tracks) if tracks else None,
                 "names": json.dumps(names, ensure_ascii=False) if names else None, "user": getUser()},
            )
    return jsonify(ok=True)


@admin_blueprint.route("/vagonweb")
@owner_required
def vagonweb_admin():
    return render_template(
        "admin/vagonweb.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        country_list=get_all_countries(),
        isCurrent=has_current_trip(),
        **session["userinfo"],
        **lang[session["userinfo"]["lang"]],
    )


__all__ = [
    "admin_blueprint",
    "operators_api_blueprint",
    "trainsets_admin_blueprint",
    "wagons_admin_blueprint",
]
