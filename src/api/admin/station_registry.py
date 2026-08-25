"""Admin panel for the station registry (migrations 0058-0061).

The registry mostly runs itself; this covers the judgement it cannot make alone — the
unresolved-label queue, merging two rows that are one station, and curated_name for the
names the rules get wrong. Every action is reversible and none touches a user's trips.
"""

import logging

from flask import Blueprint, current_app, jsonify, render_template, request, session

from src.pg import pg_session
from src.station_names import international_name
from src.station_osm import (
    alias_rows_from_tags,
    drain_enrichment_queue,
    enrich_stations,
    fetch_osm_objects,
)
from src.stations import (
    add_aliases,
    find_station,
    check_labels_consistency,
    merge_stations,
    modes_in_use,
    count_unresolved,
    delete_station,
    label_location,
    registry_stats,
    resync_station,
    stations_holding_alias,
    strip_flag,
    unresolved_labels,
    upsert_station,
)
from src.station_seed import request_stop, run_status, start_seed_run
from src.consts import TRIP_TYPE_ICONS
from src.utils import admin_required, getUser, has_current_trip, lang, owner_required

logger = logging.getLogger(__name__)

station_registry_blueprint = Blueprint("station_registry", __name__)


@station_registry_blueprint.route("")
@admin_required
def station_registry():
    # Stats are loaded from /stats rather than passed: the name `stats` is already a navbar
    # translation key and would collide with the lang/userinfo splat below.
    return render_template(
        "admin/station_registry.html",
        nav="bootstrap/navigation.html",
        username=getUser(),
        trip_type_icons=TRIP_TYPE_ICONS,
        # Pulls in leafletLayout.html for the detail modal's position map.
        leaflet=True,
        isCurrent=has_current_trip(),
        **lang[session["userinfo"]["lang"]],
        **session["userinfo"],
    )


@station_registry_blueprint.route("/stats")
@admin_required
def stats():
    return jsonify(registry_stats())


@station_registry_blueprint.route("/consistency")
@admin_required
def consistency():
    """Check the label cache against a fresh resolution. A full pass, so it is a button."""
    return jsonify(check_labels_consistency())


@station_registry_blueprint.route("/unresolved")
@admin_required
def unresolved():
    """The work queue: labels that resolve to no station, costliest first."""
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = max(int(request.args.get("offset", 0)), 0)
    search = request.args.get("q")
    mode = request.args.get("mode")
    status = request.args.get("status")
    return jsonify(
        {
            "labels": unresolved_labels(
                limit=limit, search=search, offset=offset, mode=mode, status=status
            ),
            "total": count_unresolved(search=search, mode=mode, status=status),
            "offset": offset,
        }
    )


@station_registry_blueprint.route("/label-location")
@admin_required
def label_location_route():
    """Where the trips using a label actually start or end.

    Fetched when the resolve modal opens rather than with the queue: it is a percentile over
    every path endpoint using the spelling, which is worth paying for once on the label being
    worked and not a hundred times per page.
    """
    label = request.args.get("label") or ""
    mode = request.args.get("mode") or "train"
    return jsonify({"location": label_location(label, mode)})


@station_registry_blueprint.route("/modes")
@admin_required
def modes():
    """The modes present, with their counts, for the filter."""
    return jsonify({"modes": modes_in_use()})


@station_registry_blueprint.route("/stations")
@admin_required
def list_stations():
    """Registered stations, newest first, with how many trip endpoints each resolves."""
    search = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = max(int(request.args.get("offset", 0)), 0)
    mode = (request.args.get("mode") or "").strip()

    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT s.station_id,
                   COALESCE(s.curated_name, s.name_intl) AS name,
                   s.name_intl,
                   s.curated_name,
                   s.name_local,
                   s.station_type,
                   s.country_code,
                   s.wikidata,
                   s.uic_ref,
                   s.enriched_at,
                   s.superseded_by,
                   (SELECT count(*) FROM station_aliases a WHERE a.station_id = s.station_id)
                       AS aliases,
                   (SELECT COALESCE(sum(l.occurrences), 0) FROM station_labels l
                    WHERE l.station_id = s.station_id) AS endpoints
            FROM stations s
            WHERE (:mode = '' OR s.station_type = :mode)
              AND (:search = ''
                   OR EXISTS (
                       SELECT 1 FROM station_aliases a
                       WHERE a.station_id = s.station_id
                         AND station_fold(a.alias) LIKE '%' || station_fold(:search) || '%'
                   ))
            ORDER BY (SELECT COALESCE(sum(l.occurrences), 0) FROM station_labels l
                      WHERE l.station_id = s.station_id) DESC, s.station_id DESC
            LIMIT :limit OFFSET :offset
            """,
            {"search": search, "limit": limit, "offset": offset, "mode": mode},
        ).fetchall()
        total = pg.execute(
            """
            SELECT count(*) FROM stations s
            WHERE (:mode = '' OR s.station_type = :mode)
              AND (:search = ''
                   OR EXISTS (
                       SELECT 1 FROM station_aliases a
                       WHERE a.station_id = s.station_id
                         AND station_fold(a.alias) LIKE '%' || station_fold(:search) || '%'
                   ))
            """,
            {"search": search, "mode": mode},
        ).scalar()
    return jsonify(
        {
            "stations": [dict(row._mapping) for row in rows],
            "total": total,
            "offset": offset,
        }
    )


def _station_payload(station_id):
    """The station page's data. Shared with the registration preview so both render the same."""
    with pg_session() as pg:
        station = pg.execute(
            "SELECT * FROM stations WHERE station_id = :id", {"id": station_id}
        ).fetchone()
        if station is None:
            return None

        aliases = pg.execute(
            """
            SELECT a.alias_id, a.alias, a.normalized, a.kind, a.lang,
                   (SELECT COALESCE(sum(l.occurrences), 0) FROM station_labels l
                    WHERE l.station_id = a.station_id
                      AND l.normalized = a.normalized) AS endpoints
            FROM station_aliases a
            WHERE a.station_id = :id
            ORDER BY (a.kind = 'intl') DESC, (a.kind = 'local') DESC, a.alias
            """,
            {"id": station_id},
        ).fetchall()
        objects = pg.execute(
            "SELECT osm_type, osm_id FROM station_osm_objects WHERE station_id = :id"
            " ORDER BY osm_type, osm_id",
            {"id": station_id},
        ).fetchall()

    return {
        "station": dict(station._mapping),
        "aliases": [dict(row._mapping) for row in aliases],
        "osm_objects": [dict(row._mapping) for row in objects],
    }


@station_registry_blueprint.route("/station/<int:station_id>")
@admin_required
def station_detail(station_id):
    payload = _station_payload(station_id)
    if payload is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(payload)


@station_registry_blueprint.route("/station/<int:station_id>/curate", methods=["POST"])
@admin_required
def curate(station_id):
    """Set or clear the curated name. It outranks the generated one everywhere."""
    name = (request.json or {}).get("curated_name", "").strip()
    with pg_session() as pg:
        pg.execute(
            "UPDATE stations SET curated_name = :name WHERE station_id = :id",
            {"name": name or None, "id": station_id},
        )
        # The curated name is itself a spelling this station should be findable by.
        if name:
            add_aliases(station_id, [(name, "alias", None)], pg_session_=pg)
    # Trips already using this spelling should pick the station up.
    return jsonify({"success": True, "trips_resynced": resync_station(station_id)})


@station_registry_blueprint.route("/station/<int:station_id>/position", methods=["POST"])
@admin_required
def set_position(station_id):
    """Move the station's dot, or clear the move and fall back to the OSM position.

    The correction lives in curated_lat/curated_lng, never overwriting the OSM value, so
    re-enrichment can refresh the position without destroying it. See migration 0058.
    """
    body = request.json or {}
    if body.get("clear"):
        lat = lng = None
    else:
        try:
            lat, lng = float(body["lat"]), float(body["lng"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"success": False, "error": "lat and lng required"}), 400
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return jsonify({"success": False, "error": "coordinates out of range"}), 400

    with pg_session() as pg:
        pg.execute(
            "UPDATE stations SET curated_lat = :lat, curated_lng = :lng"
            " WHERE station_id = :id",
            {"lat": lat, "lng": lng, "id": station_id},
        )
        row = pg.execute(
            "SELECT effective_lat, effective_lng, curated_lat FROM stations"
            " WHERE station_id = :id",
            {"id": station_id},
        ).fetchone()

    return jsonify(
        {
            "success": True,
            "lat": row["effective_lat"],
            "lng": row["effective_lng"],
            "curated": row["curated_lat"] is not None,
        }
    )


@station_registry_blueprint.route("/station/<int:station_id>/alias", methods=["POST"])
@admin_required
def add_alias(station_id):
    """Attach a spelling to a station. This is how the unresolved queue is worked."""
    body = request.json or {}
    alias = (body.get("alias") or "").strip()
    alias_lang = (body.get("lang") or "").strip().lower() or None
    if not alias:
        return jsonify({"success": False, "error": "empty alias"}), 400

    # With a language, the spelling *is* the station's name in that language and is shown to
    # readers of it — the only way to add one OSM and Wikidata do not carry.
    kind = "lang" if alias_lang else "alias"

    # The queue shows labels flag and all; storing it would put a misleading spelling in the
    # alias list, even though station_normalize would ignore it.
    added = add_aliases(station_id, [(strip_flag(alias), kind, alias_lang)])
    if alias_lang:
        _record_language_name(station_id, alias_lang, strip_flag(alias))
    return jsonify(
        {
            "success": True,
            "added": added,
            "trips_resynced": resync_station(station_id),
        }
    )


def _record_language_name(station_id, alias_lang, name):
    """Also store an admin-supplied name in `stations.names`, as `name:<lang>`.

    station_aliases makes a spelling findable; `names` is what display_name() shows. Without
    this an admin's added name would search correctly and never be displayed. Merged, not
    assigned, so a later re-enrichment does not drop it.
    """
    with pg_session() as pg:
        pg.execute(
            "UPDATE stations"
            " SET names = COALESCE(names, '{}'::jsonb) || jsonb_build_object(:key, :value)"
            " WHERE station_id = :id",
            {"key": f"name:{alias_lang}", "value": name, "id": station_id},
        )


@station_registry_blueprint.route("/alias/<int:alias_id>", methods=["DELETE"])
@admin_required
def delete_alias(alias_id):
    """Drop a spelling. Trips using it fall back to being unresolved, never to a wrong place."""
    with pg_session() as pg:
        row = pg.execute(
            "SELECT station_id, kind FROM station_aliases WHERE alias_id = :id",
            {"id": alias_id},
        ).fetchone()
        if row is None:
            return jsonify({"success": False, "error": "not found"}), 404
        # The station's own names are how it is found at all; removing one would strand it.
        if row["kind"] in ("intl", "local"):
            return jsonify(
                {
                    "success": False,
                    "error": "that is one of the station's own names, not an added alias",
                }
            ), 400
        station_id = row["station_id"]
        pg.execute("DELETE FROM station_aliases WHERE alias_id = :id", {"id": alias_id})
    return jsonify({"success": True, "trips_resynced": resync_station(station_id)})


@station_registry_blueprint.route("/station/<int:station_id>/enrich", methods=["POST"])
@admin_required
def enrich_one(station_id):
    """Re-fetch this station's OSM tags now, rather than waiting for the cron drain."""
    try:
        return jsonify({"success": True, **enrich_stations([station_id])})
    except Exception as e:
        logger.warning(f"Manual enrichment of station {station_id} failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 502


# ── Seeding ──────────────────────────────────────────────────────────────────────────────
#
# Owner-only, unlike the rest of the panel: this registers stations unattended, at a rate no
# one is reading, and it spends the Photon that serves the live autocomplete. Everything else
# here is one admin deciding one label.


@station_registry_blueprint.route("/seed", methods=["GET"])
@owner_required
def seed_status():
    return jsonify(run_status())


@station_registry_blueprint.route("/seed/start", methods=["POST"])
@owner_required
def seed_start():
    body = request.json or {}
    try:
        limit = min(max(int(body.get("limit", 100)), 1), 20000)
        delay = min(max(float(body.get("delay", 0.5)), 0.1), 10.0)
        min_occurrences = max(int(body.get("min_occurrences", 1)), 1)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "bad parameters"}), 400

    result = start_seed_run(
        current_app._get_current_object(),
        getUser(),
        limit=limit,
        delay=delay,
        min_occurrences=min_occurrences,
        dry_run=bool(body.get("dry_run")),
    )
    return jsonify(result), (200 if result.get("success") else 409)


@station_registry_blueprint.route("/seed/stop", methods=["POST"])
@owner_required
def seed_stop():
    """Ask the run to finish after the label it is on. Nothing in progress is lost."""
    return jsonify(request_stop())


@station_registry_blueprint.route("/enrich-queue", methods=["POST"])
@admin_required
def enrich_queue():
    """Drain part of the enrichment queue. Bounded, so this cannot become a long request."""
    batches = min(int((request.json or {}).get("batches", 1)), 5)
    try:
        return jsonify({"success": True, **drain_enrichment_queue(max_batches=batches)})
    except Exception as e:
        logger.warning(f"Enrichment drain failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 502


@station_registry_blueprint.route("/station/<int:station_id>", methods=["DELETE"])
@admin_required
def remove_station(station_id):
    """Delete a station; its spellings return to the unresolved queue. No trip is touched."""
    result = delete_station(station_id)
    return jsonify(result), (200 if result.get("success") else 404)


@station_registry_blueprint.route("/merge", methods=["POST"])
@admin_required
def merge():
    """Fold one station into another. Nothing is deleted; the source is marked superseded."""
    body = request.json or {}
    try:
        source_id, target_id = int(body["source_id"]), int(body["target_id"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"success": False, "error": "source_id and target_id required"}), 400
    if source_id == target_id:
        return jsonify({"success": False, "error": "cannot merge a station into itself"}), 400
    return jsonify(merge_stations(source_id, target_id))


@station_registry_blueprint.route("/register/preview", methods=["POST"])
@admin_required
def register_preview():
    """Everything the station page shows, for a station that does not exist yet.

    Shaped exactly like /station/<id> so one renderer draws both, and the admin decides on the
    real page rather than a summary of it. That needs the OSM tags up front, so this fetches
    them; saving fetches them again to write them, which is one extra Overpass call per manual
    registration.

    A fetch failure stops the preview rather than degrading it: a page with no identity looks
    exactly like a station that has none, and that is not a thing to register on.
    """
    body = request.json or {}
    raw_label = (body.get("raw_name") or "").strip()
    name = strip_flag(body.get("name") or "") or strip_flag(raw_label)
    station_type = body.get("station_type") or "train"
    alias = strip_flag(raw_label)
    osm_type, osm_id = body.get("osm_type"), body.get("osm_id")

    endpoints = _label_endpoints(raw_label, station_type)
    existing = find_station(station_type=station_type, osm_type=osm_type, osm_id=osm_id)

    # An existing station already has a page; return it verbatim so the two cases render from
    # identical data and cannot drift apart.
    if existing:
        payload = _station_payload(existing)
        payload.update(
            {
                "action": "attach",
                "alias": alias,
                "blocked_by": _blocked_by(alias, station_type, existing),
                "endpoints": endpoints,
            }
        )
        return jsonify(payload)

    tags, position = {}, {}
    if osm_type and osm_id:
        try:
            fetched = fetch_osm_objects([(osm_type, osm_id)])
            got = fetched.get((osm_type, int(osm_id)), {})
            tags = got.get("tags", {}) or {}
            position = {"lat": got.get("lat"), "lng": got.get("lng")}
        except Exception as e:
            logger.warning(f"Preview could not reach Overpass for {osm_type}{osm_id}: {e}")
            return jsonify(
                {
                    "success": False,
                    "error": f"Could not read this object's OSM tags: {e}. "
                             f"Nothing was written — try again in a moment.",
                }
            ), 502

    name_local = tags.get("name") or body.get("name_local")
    name_intl = international_name(
        name_local, tags.get("name:en"), country_code=body.get("country_code"), tags=tags
    ) or name

    lat = position.get("lat") if position.get("lat") is not None else body.get("lat")
    lng = position.get("lng") if position.get("lng") is not None else body.get("lng")

    # The spellings this station would answer to, in the shape the page lists them.
    aliases, seen = [], set()
    for a, kind, lang in alias_rows_from_tags(tags, name_intl):
        key = (a or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        aliases.append({"alias_id": None, "alias": a, "kind": kind, "lang": lang,
                        "endpoints": 0})
    if alias and alias.strip().lower() not in seen:
        aliases.append({"alias_id": None, "alias": alias, "kind": "alias", "lang": None,
                        "endpoints": endpoints})

    return jsonify(
        {
            "action": "create",
            "alias": alias,
            "blocked_by": _blocked_by(alias, station_type, None),
            "endpoints": endpoints,
            "station": {
                "station_id": None,
                "station_type": station_type,
                "name_intl": name_intl,
                "name_local": name_local,
                "curated_name": None,
                "wikidata": tags.get("wikidata"),
                "uic_ref": tags.get("uic_ref"),
                "country_code": body.get("country_code"),
                "lat": lat,
                "lng": lng,
                "curated_lat": None,
                "curated_lng": None,
                "effective_lat": lat,
                "effective_lng": lng,
                "enriched_at": None,
                "superseded_by": None,
            },
            "aliases": aliases,
            "osm_objects": (
                [{"osm_type": osm_type, "osm_id": osm_id}] if osm_type and osm_id else []
            ),
        }
    )


def _blocked_by(alias, station_type, existing):
    """Other live stations already answering to this spelling, which would make it ambiguous."""
    if not alias:
        return []
    return [h for h in stations_holding_alias(alias, station_type) if h != existing]


def _label_endpoints(raw_label, station_type):
    with pg_session() as pg:
        return int(
            pg.execute(
                "SELECT COALESCE(sum(occurrences), 0) FROM station_labels"
                " WHERE normalized = station_normalize(:label)"
                "   AND station_type = station_type_bucket(:station_type)",
                {"label": raw_label, "station_type": station_type},
            ).scalar()
            or 0
        )


@station_registry_blueprint.route("/register", methods=["POST"])
@admin_required
def register():
    """Create a station for an unresolved label, from a chosen search result.

    The queue's main action: the label becomes an alias of the picked place, resolving every
    trip using that spelling at once.
    """
    body = request.json or {}
    raw_label = (body.get("raw_name") or "").strip()
    name = strip_flag(body.get("name") or "") or strip_flag(raw_label)
    if not name:
        return jsonify({"success": False, "error": "no name"}), 400

    station_type = body.get("station_type") or "train"

    # Refuse to give this spelling to a second station: a search routinely offers several rows
    # that are the same place, and registering two attaches the label to both, which resolves
    # to neither. Measured: 2,100 endpoints stranded on one label. The browser locks the result
    # list too, but a second tab or a direct call bypasses that; this is the check that holds.
    if raw_label:
        holders = stations_holding_alias(strip_flag(raw_label), station_type)
        if holders:
            return jsonify(
                {
                    "success": False,
                    "error": (
                        f"“{strip_flag(raw_label)}” is already a spelling of station "
                        f"{holders[0]}. Registering it again would make it ambiguous and "
                        f"resolve to neither — merge the two stations instead."
                    ),
                    "station_id": holders[0],
                }
            ), 409

    station_id = upsert_station(
        station_type=station_type,
        name_intl=international_name(
            body.get("name_local"), name, country_code=body.get("country_code")
        )
        or name,
        name_local=body.get("name_local"),
        osm_type=body.get("osm_type"),
        osm_id=body.get("osm_id"),
        country_code=body.get("country_code"),
        lat=body.get("lat"),
        lng=body.get("lng"),
    )
    if station_id is None:
        return jsonify({"success": False, "error": "could not register"}), 400

    # The typed label becomes a spelling of this station — often not the station's own name,
    # and what actually resolves the trips.
    if raw_label:
        add_aliases(station_id, [(strip_flag(raw_label), "alias", None)])

    resynced = resync_station(station_id)

    # Fetch the tags now rather than leaving the row for the background thread. The preview
    # already showed the admin this station's identity and spellings; saving has to actually
    # write them, or the page they approved is not the page they get.
    enriched = {}
    try:
        enriched = enrich_stations([station_id])
    except Exception as e:
        logger.warning(f"Station {station_id} registered but enrichment failed: {e}")

    # A moved marker goes to the curated position, not to lat/lng: enrichment refreshes those
    # from OSM and would put the pin straight back where the admin moved it from.
    curated_lat, curated_lng = body.get("curated_lat"), body.get("curated_lng")
    if curated_lat is not None and curated_lng is not None:
        with pg_session() as pg:
            pg.execute(
                "UPDATE stations SET curated_lat = :lat, curated_lng = :lng"
                " WHERE station_id = :id",
                {"lat": float(curated_lat), "lng": float(curated_lng), "id": station_id},
            )

    return jsonify(
        {
            "success": True,
            "station_id": station_id,
            "trips_resynced": resynced,
            **enriched,
        }
    )
