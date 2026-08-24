"""Enriching registry stations from their OSM tags, which Photon does not index.

Measured over 60 stations: wikidata 97%, alt_name 37%, uic_ref 35%, int_name 3%, and 2.4
name:* tags each. That sample skewed European and coverage falls off sharply outside it —
Seoul Station has 105 name:* tags and neither wikidata nor uic_ref, on the node or its
stop_area relations. So identity falls back through (osm_type, osm_id), and the sibling
lookup falls back to the name.

This project has already been banned from one free geocoder for volume, so: batched requests
only, never on the request path (stations enter with enriched_at NULL and a background thread
drains them — Trainlog runs no scheduler), a real User-Agent, and a pause between batches.
The per-object OSM API is reserved for admin-triggered single re-enrichment.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone

import requests

from src.pg import get_or_create_pg_session
from src.sql.stations import resolve_station_labels_query
from src.station_names import international_name
from src.stations import add_aliases

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_API_URL = "https://api.openstreetmap.org/api/0.6"

USER_AGENT = "Trainlog/1.0 (+https://trainlog.me; station registry enrichment)"

# Measured: 60 ids answer in 2.19s. Larger batches risk hitting Overpass's timeout and losing
# the whole batch rather than part of it.
BATCH_SIZE = 60

# Overpass advertises two slots; this is not latency-sensitive work.
PAUSE_BETWEEN_BATCHES_S = 2.0

OVERPASS_TIMEOUT_S = 90

_OSM_TYPE_TO_OVERPASS = {"N": "node", "W": "way", "R": "relation"}
_OVERPASS_TYPE_TO_OSM = {"node": "N", "way": "W", "relation": "R"}

# Tags worth keeping verbatim in stations.names, beyond every name:* key.
_EXTRA_NAME_TAGS = ("int_name", "alt_name", "official_name", "short_name", "loc_name")


class OverpassError(Exception):
    """Overpass could not be reached or refused the query."""


def _overpass(query: str) -> dict:
    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=OVERPASS_TIMEOUT_S,
    )
    if response.status_code != 200:
        raise OverpassError(f"Overpass returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as e:
        # Overpass answers rate limiting and overload with an HTML page, not JSON.
        raise OverpassError(f"Overpass returned a non-JSON body: {e}") from e


def extract_names(tags: dict) -> dict:
    """The naming tags worth storing, from a full OSM tag dict."""
    return {
        key: value
        for key, value in tags.items()
        if (key.startswith("name:") or key in _EXTRA_NAME_TAGS) and value
    }


def alias_rows_from_tags(tags: dict, name_intl: str | None):
    """Every spelling this station should be findable by, as (alias, kind, lang) triples."""
    rows = []
    if name_intl:
        rows.append((name_intl, "intl", None))
    if tags.get("name"):
        rows.append((tags["name"], "local", None))
    if tags.get("int_name"):
        rows.append((tags["int_name"], "int_name", None))
    for key in ("alt_name", "official_name", "short_name", "loc_name"):
        value = tags.get(key)
        if not value:
            continue
        # These tags are semicolon-separated lists by OSM convention.
        for part in str(value).split(";"):
            if part.strip():
                rows.append(
                    (part.strip(), "alt_name" if key == "alt_name" else "official", None)
                )
    for key, value in tags.items():
        if key.startswith("name:") and value:
            rows.append((value, "lang", key[len("name:") :]))
    return rows


def _fetch_objects(objects) -> dict:
    """Fetch OSM tags and position for (osm_type, osm_id) pairs in one Overpass request.

    Returns {(osm_type, osm_id): {"tags": {...}, "lat": float|None, "lng": float|None}}.

    `out body center` rather than `out tags` so the position comes back too — a node carries
    lat/lon, a way or relation gets a `center`.
    """
    by_type = {}
    for osm_type, osm_id in objects:
        overpass_type = _OSM_TYPE_TO_OVERPASS.get(osm_type)
        if overpass_type:
            by_type.setdefault(overpass_type, []).append(int(osm_id))
    if not by_type:
        return {}

    clauses = "".join(
        f"{overpass_type}(id:{','.join(str(i) for i in ids)});"
        for overpass_type, ids in by_type.items()
    )
    data = _overpass(
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];({clauses});out body center;"
    )

    result = {}
    for element in data.get("elements", []):
        if element.get("type") not in _OVERPASS_TYPE_TO_OSM:
            continue
        center = element.get("center") or {}
        result[(_OVERPASS_TYPE_TO_OSM[element["type"]], element["id"])] = {
            "tags": element.get("tags", {}),
            "lat": element.get("lat", center.get("lat")),
            "lng": element.get("lon", center.get("lon")),
        }
    return result


def _fetch_sibling_objects(stations) -> dict:
    """Find every OSM object belonging to the same station as each given one.

    `stations` is an iterable of (station_id, wikidata, uic_ref, lat, lng, name_local).
    Returns {station_id: [(osm_type, osm_id), …]}.

    Every clause is bounded by `around` the station's coordinates: an unbounded
    nwr["wikidata"=…] would be a planet-wide scan per station.
    """
    clauses, wanted = [], {}
    for station_id, wikidata, uic_ref, lat, lng, name_local in stations:
        if lat is None or lng is None:
            continue
        # Both tags, not whichever comes first: platform and stop nodes usually carry uic_ref
        # but not wikidata, so asking only about wikidata found one object out of four.
        if wikidata:
            clauses.append(f'nwr(around:2000,{lat},{lng})["wikidata"="{wikidata}"];')
            wanted[("wikidata", wikidata)] = station_id
        if uic_ref:
            clauses.append(f'nwr(around:2000,{lat},{lng})["uic_ref"="{uic_ref}"];')
            wanted[("uic_ref", uic_ref)] = station_id

        # Neither tag (common outside Europe — see the module docstring): fall back to an
        # identical name within 500m. Weaker evidence than a shared QID, but two distinct
        # stations with the same name that close together is not a real situation.
        if not wikidata and not uic_ref and name_local:
            # Unescaped quotes make the query malformed and fail the whole batch.
            escaped = name_local.replace("\\", "\\\\").replace('"', '\\"')
            # Any transport object, not just ["railway"]: a bus stop is highway=bus_stop, a
            # ferry terminal amenity=ferry_terminal. Those pools need this fallback most.
            transport = "".join(
                f'nwr(around:500,{lat},{lng})["name"="{escaped}"]["{key}"];'
                for key in ("railway", "highway", "amenity", "aerialway", "public_transport")
            )
            clauses.append(transport)
            wanted[("name", name_local)] = station_id
    if not clauses:
        return {}

    data = _overpass(
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];({''.join(clauses)});out tags;"
    )

    result = {}
    for element in data.get("elements", []):
        osm_type = _OVERPASS_TYPE_TO_OSM.get(element.get("type"))
        if not osm_type:
            continue
        tags = element.get("tags", {})
        # The union response does not say which clause produced which element, so the
        # identifying tag is the only link back to the station.
        station_id = None
        for key in ("wikidata", "uic_ref", "name"):
            if tags.get(key) and (key, tags[key]) in wanted:
                station_id = wanted[(key, tags[key])]
                break
        if station_id is not None:
            result.setdefault(station_id, []).append((osm_type, element["id"]))
    return result


def _identity_to_write(pg, station_id, wikidata, uic_ref):
    """Drop any identity anchor a different live station in the same pool already holds.

    Returns (wikidata, uic_ref) with the colliding ones replaced by None. Two live stations
    claiming one anchor are the same place and want merging, which is an admin's decision —
    but attempting the write violates the identity indexes from 0062, so it cannot simply be
    tried and hoped for.
    """
    kept = []
    for column, value in (("wikidata", wikidata), ("uic_ref", uic_ref)):
        if not value:
            kept.append(None)
            continue
        holder = pg.execute(
            f"SELECT station_id FROM stations"
            f" WHERE {column} = :value"
            f"   AND station_type = (SELECT station_type FROM stations WHERE station_id = :id)"
            f"   AND station_id <> :id"
            f"   AND superseded_by IS NULL"
            f" LIMIT 1",
            {"value": value, "id": station_id},
        ).scalar()
        if holder:
            logger.warning(
                f"Station {station_id} reports {column}={value}, already held by live "
                f"station {holder}; not writing it. These are likely the same place — "
                f"merge them in the admin panel."
            )
            kept.append(None)
        else:
            kept.append(value)
    return kept[0], kept[1]


def enrich_stations(station_ids, map_objects: bool = True, pg_session_=None) -> dict:
    """Fetch and store the OSM tags for these stations. Batched; safe to call repeatedly.

    Updates wikidata, uic_ref, names, name_intl and country_code, records every spelling in
    station_aliases, and stamps enriched_at so the station leaves the queue.

    Nothing here may leave a station queued forever, because the drain takes the oldest rows
    first: one row that can never succeed blocks every row behind it, retrying on each pass.
    So a station whose object is gone from OSM is still stamped, and each station's writes are
    wrapped in a SAVEPOINT — one failure rolls back that station alone and the batch carries on.
    """
    station_ids = [int(s) for s in station_ids]
    if not station_ids:
        return {"enriched": 0, "objects_mapped": 0, "failed": 0}

    enriched = objects_mapped = failed = 0

    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            "SELECT station_id, osm_type, osm_id, name_intl, country_code,"
            " effective_lat AS lat, effective_lng AS lng"
            " FROM stations WHERE station_id = ANY(:ids)",
            {"ids": station_ids},
        ).fetchall()

        targets = [(r["osm_type"], r["osm_id"]) for r in rows if r["osm_id"] is not None]
        objects = _fetch_objects(targets) if targets else {}

        for row in rows:
            fetched = objects.get((row["osm_type"], row["osm_id"]), {})
            tags = fetched.get("tags", {})
            names = extract_names(tags)

            name_local = tags.get("name") or None

            # Recompute the display name only where the tags know something the autocomplete
            # could not: int_name and name:xx-Latn, which Photon does not carry. Otherwise the
            # autocomplete's answer is better, because it had the city — recomputing
            # unconditionally rewrote "Paris - Gare de Lyon" to "Gare de Lyon".
            has_better_name_tag = bool(tags.get("int_name")) or any(
                key.startswith("name:") and key.endswith("-Latn") and value
                for key, value in tags.items()
            )
            name_intl = (
                international_name(
                    name_local,
                    tags.get("name:en"),
                    country_code=row["country_code"],
                    tags=tags,
                )
                if has_better_name_tag
                else None
            )

            # An identity anchor another *live* station in this pool already holds is dropped
            # rather than written. Two live stations claiming one QID are the same place and
            # want merging, which is an admin's call — but writing it would violate
            # stations_wikidata_key and, before SAVEPOINTs, took the whole batch down with it.
            wikidata, uic_ref = _identity_to_write(
                pg, row["station_id"], tags.get("wikidata"), tags.get("uic_ref")
            )

            savepoint = pg.begin_nested()
            try:
                # `names` is merged, not assigned: an admin's added language name lives there
                # too (see _record_language_name), and assigning dropped it on the next pass —
                # or blanked the column outright when Overpass returned nothing.
                #
                # The position is refreshed from OSM, making a bad one self-correcting:
                # measured, one station in 46 sat 9.7km from its own node, and those
                # coordinates anchor the geometry backfill, the duplicate radius and the
                # sibling `around` bound. Admin corrections live in curated_* and are untouched.
                pg.execute(
                    """
                    UPDATE stations SET
                        wikidata    = COALESCE(:wikidata, wikidata),
                        uic_ref     = COALESCE(:uic_ref, uic_ref),
                        names       = COALESCE(names, '{}'::jsonb) || CAST(:names AS jsonb),
                        name_local  = COALESCE(:name_local, name_local),
                        name_intl   = COALESCE(NULLIF(:name_intl, ''), name_intl),
                        lat         = COALESCE(:lat, lat),
                        lng         = COALESCE(:lng, lng),
                        enriched_at = :now
                    WHERE station_id = :station_id
                    """,
                    {
                        "station_id": row["station_id"],
                        "wikidata": wikidata,
                        "uic_ref": uic_ref,
                        "names": json.dumps(names, ensure_ascii=False),
                        "name_local": name_local,
                        "name_intl": name_intl or "",
                        "lat": fetched.get("lat"),
                        "lng": fetched.get("lng"),
                        "now": datetime.now(timezone.utc),
                    },
                )

                if tags:
                    add_aliases(
                        row["station_id"],
                        alias_rows_from_tags(tags, name_intl),
                        pg_session_=pg,
                    )
                savepoint.commit()
                enriched += 1
            except Exception as e:
                savepoint.rollback()
                failed += 1
                logger.warning(f"Could not enrich station {row['station_id']}: {e}")
                # Stamp it anyway, so it leaves the queue. The drain takes the oldest rows
                # first, so a row that fails for a durable reason would otherwise be retried
                # on every pass forever. The station keeps the name the autocomplete gave it
                # and works; an admin can re-enrich it from the panel.
                stamp = pg.begin_nested()
                try:
                    pg.execute(
                        "UPDATE stations SET enriched_at = :now WHERE station_id = :id",
                        {"now": datetime.now(timezone.utc), "id": row["station_id"]},
                    )
                    stamp.commit()
                except Exception:
                    stamp.rollback()

        if map_objects:
            sibling_targets = pg.execute(
                "SELECT station_id, wikidata, uic_ref, effective_lat AS lat,"
                " effective_lng AS lng, name_local"
                " FROM stations"
                " WHERE station_id = ANY(:ids)"
                "   AND (wikidata IS NOT NULL OR uic_ref IS NOT NULL"
                "        OR name_local IS NOT NULL)",
                {"ids": station_ids},
            ).fetchall()
            if sibling_targets:
                siblings = _fetch_sibling_objects(
                    [
                        (
                            r["station_id"],
                            r["wikidata"],
                            r["uic_ref"],
                            r["lat"],
                            r["lng"],
                            r["name_local"],
                        )
                        for r in sibling_targets
                    ]
                )
                for station_id, objects in siblings.items():
                    for osm_type, osm_id in objects:
                        pg.execute(
                            "INSERT INTO station_osm_objects (osm_type, osm_id, station_id)"
                            " VALUES (:osm_type, :osm_id, :station_id)"
                            " ON CONFLICT (osm_type, osm_id) DO NOTHING",
                            {
                                "osm_type": osm_type,
                                "osm_id": osm_id,
                                "station_id": station_id,
                            },
                        )
                        objects_mapped += 1

        # Re-resolve the spellings these stations can now claim; without this the newly added
        # aliases sit inert until a full rebuild. Measured: 41 labels and 11,453 trip
        # endpoints unresolved against stations already holding their exact spelling.
        if enriched:
            pg.execute(
                resolve_station_labels_query(scoped=True), {"station_ids": station_ids}
            )

    return {"enriched": enriched, "objects_mapped": objects_mapped, "failed": failed}


def start_station_enricher(app, interval_s: int = 600):
    """Drain the enrichment queue periodically, in a background thread.

    Follows start_email_listener(): Trainlog runs no scheduler. Each gunicorn worker starts
    one, which is harmless — the queue is a table column (`enriched_at IS NULL`), not
    in-memory state, so they contend for rows rather than a lock, a restart mid-drain loses
    nothing, and the batch size and pause keep the combined rate inside Overpass's limits.
    """
    def loop():
        # Let the app finish booting; nothing here is urgent.
        time.sleep(60)
        while True:
            try:
                with app.app_context():
                    result = drain_enrichment_queue(max_batches=5)
                if result["enriched"]:
                    logger.info(
                        f"Station enricher: {result['enriched']} station(s), "
                        f"{result['objects_mapped']} OSM object(s)."
                    )
            except Exception as e:
                # Never let this thread die: the queue is durable, so the next pass retries.
                logger.warning(f"Station enricher pass failed: {e}")
            time.sleep(interval_s)

    threading.Thread(target=loop, daemon=True, name="station-enricher").start()


def drain_enrichment_queue(
    max_batches: int | None = None, map_objects: bool = True, pg_session_=None
) -> dict:
    """Enrich stations awaiting it, in batches.

    `max_batches` bounds one run so a tick cannot become an hour of Overpass traffic. A failed
    batch is left queued rather than retried immediately.
    """
    totals = {
        "enriched": 0,
        "objects_mapped": 0,
        "batches": 0,
        "failed": 0,
        "failed_batches": 0,
    }

    while max_batches is None or totals["batches"] < max_batches:
        with get_or_create_pg_session(pg_session_) as pg:
            pending = [
                row[0]
                for row in pg.execute(
                    "SELECT station_id FROM stations WHERE enriched_at IS NULL"
                    " ORDER BY station_id LIMIT :limit",
                    {"limit": BATCH_SIZE},
                ).fetchall()
            ]
        if not pending:
            break

        try:
            result = enrich_stations(
                pending, map_objects=map_objects, pg_session_=pg_session_
            )
            totals["enriched"] += result["enriched"]
            totals["objects_mapped"] += result["objects_mapped"]
            totals["failed"] += result["failed"]
        except (OverpassError, requests.RequestException) as e:
            # A network failure is transient, so this batch is left queued deliberately —
            # unlike a per-station failure, which is stamped so it cannot block the queue.
            logger.warning(f"Enrichment batch failed, leaving it queued: {e}")
            totals["failed_batches"] += 1
            break

        totals["batches"] += 1
        time.sleep(PAUSE_BETWEEN_BATCHES_S)

    return totals
