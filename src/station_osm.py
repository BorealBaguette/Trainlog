"""Enriching registry stations from their OSM tags, which Photon does not index.

Overpass is a shared public service, so it is never called on the request path: stations
enter with enriched_at NULL and a background thread drains them in batches.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone

import requests
from sqlalchemy import text

import src.pg
from src.pg import get_or_create_pg_session
from src.rate_limit import RateLimited, take
from src.sql.stations import resolve_station_labels_query
from src.station_names import international_name
from src.stations import add_aliases

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_API_URL = "https://api.openstreetmap.org/api/0.6"

USER_AGENT = "Trainlog/1.0 (+https://trainlog.me; station registry enrichment)"

# Larger batches risk Overpass's timeout, losing the whole batch.
BATCH_SIZE = 60

PAUSE_BETWEEN_BATCHES_S = 2.0

# Overpass admits queries by their declared timeout, so a long one waits longer when
# the server is busy. Only the sibling search needs the long budget.
OVERPASS_TIMEOUT_S = 90
OVERPASS_LOOKUP_TIMEOUT_S = 25

# Overpass is often briefly overloaded; a short retry rides it out.
OVERPASS_ATTEMPTS = 3
OVERPASS_RETRY_BACKOFF_S = 1
OVERPASS_RETRY_STATUSES = frozenset({429, 502, 503, 504})

ENRICHER_LOCK_KEY = 0x5747_0001

_OSM_TYPE_TO_OVERPASS = {"N": "node", "W": "way", "R": "relation"}
_OVERPASS_TYPE_TO_OSM = {"node": "N", "way": "W", "relation": "R"}

_EXTRA_NAME_TAGS = ("int_name", "alt_name", "official_name", "short_name", "loc_name")


class OverpassError(Exception):
    """Overpass could not be reached or refused the query."""


def _overpass(query: str, timeout_s: int = OVERPASS_TIMEOUT_S) -> dict:
    """Run an Overpass query, retrying transient failures.

    Every attempt spends from the shared rate limit.
    """
    last = None
    for attempt in range(OVERPASS_ATTEMPTS):
        try:
            take("overpass")
        except RateLimited as e:
            raise OverpassError(str(e)) from e

        try:
            response = requests.post(
                OVERPASS_URL,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=timeout_s,
            )
        except requests.RequestException as e:
            last, retryable = OverpassError(f"Overpass request failed: {e}"), True
        else:
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as e:
                    # Overpass answers overload with an HTML page, not JSON.
                    raise OverpassError(f"Overpass returned a non-JSON body: {e}") from e
            last = OverpassError(f"Overpass returned HTTP {response.status_code}")
            retryable = response.status_code in OVERPASS_RETRY_STATUSES

        if not retryable or attempt == OVERPASS_ATTEMPTS - 1:
            raise last
        delay = OVERPASS_RETRY_BACKOFF_S * (2 ** attempt)
        logger.info(f"{last} — retrying in {delay}s")
        time.sleep(delay)
    raise last


def extract_names(tags: dict) -> dict:
    """The naming tags worth storing, from a full OSM tag dict."""
    return {
        key: value
        for key, value in tags.items()
        if (key.startswith("name:") or key in _EXTRA_NAME_TAGS) and value
    }


def alias_rows_from_tags(tags: dict, name_intl: str | None):
    """Every spelling the station should be findable by, as (alias, kind, lang)."""
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


def fetch_osm_objects(objects) -> dict:
    """Fetch tags and position for (osm_type, osm_id) pairs in one Overpass request.

    Returns {(osm_type, osm_id): {"tags": {...}, "lat": ..., "lng": ...}}.
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
        f"[out:json][timeout:{OVERPASS_LOOKUP_TIMEOUT_S}];({clauses});out body center;",
        timeout_s=OVERPASS_LOOKUP_TIMEOUT_S,
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

    `stations` holds (station_id, wikidata, uic_ref, lat, lng, name_local) tuples.
    Returns {station_id: [(osm_type, osm_id), ...]}. Every clause is bounded by `around`,
    as an unbounded tag search would scan the planet.
    """
    clauses, wanted = [], {}
    for station_id, wikidata, uic_ref, lat, lng, name_local in stations:
        if lat is None or lng is None:
            continue
        # Both tags: platform and stop nodes often carry uic_ref but not wikidata.
        if wikidata:
            clauses.append(f'nwr(around:2000,{lat},{lng})["wikidata"="{wikidata}"];')
            wanted[("wikidata", wikidata)] = station_id
        if uic_ref:
            clauses.append(f'nwr(around:2000,{lat},{lng})["uic_ref"="{uic_ref}"];')
            wanted[("uic_ref", uic_ref)] = station_id

        # Neither tag, common outside Europe: fall back to the same name within 500m.
        if not wikidata and not uic_ref and name_local:
            escaped = name_local.replace("\\", "\\\\").replace('"', '\\"')
            # Any transport object: bus stops and ferry terminals are not tagged railway.
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
        # The response does not say which clause matched; the tag is the only link back.
        station_id = None
        for key in ("wikidata", "uic_ref", "name"):
            if tags.get(key) and (key, tags[key]) in wanted:
                station_id = wanted[(key, tags[key])]
                break
        if station_id is not None:
            result.setdefault(station_id, []).append((osm_type, element["id"]))
    return result


def _identity_to_write(pg, station_id, wikidata, uic_ref):
    """Drop any identity anchor another live station in the same pool already holds.

    Returns (wikidata, uic_ref). Two stations claiming one anchor need an admin merge;
    writing it would violate the unique index.
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
    """Fetch and store the OSM tags for these stations. Safe to call repeatedly.

    Every station is stamped even when it fails, so one bad row cannot block the queue,
    and each is written in its own SAVEPOINT so a failure does not sink the batch.
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
        objects = fetch_osm_objects(targets) if targets else {}

        for row in rows:
            fetched = objects.get((row["osm_type"], row["osm_id"]), {})
            tags = fetched.get("tags", {})
            names = extract_names(tags)

            name_local = tags.get("name") or None

            # Only recompute the name when the tags know better than the autocomplete did, which
            # also had the city: int_name or name:xx-Latn.
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

            wikidata, uic_ref = _identity_to_write(
                pg, row["station_id"], tags.get("wikidata"), tags.get("uic_ref")
            )

            savepoint = pg.begin_nested()
            try:
                # `names` is merged, not replaced: it also holds names an admin added. The position is
                # refreshed from OSM; admin corrections live in curated_*.
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
                # Stamp it anyway so it leaves the queue; an admin can re-enrich it.
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
                            " ON CONFLICT (osm_type, osm_id, station_id) DO NOTHING",
                            {
                                "osm_type": osm_type,
                                "osm_id": osm_id,
                                "station_id": station_id,
                            },
                        )
                        objects_mapped += 1

        # The new aliases may resolve labels that were waiting for them.
        if enriched:
            pg.execute(
                resolve_station_labels_query(scoped=True), {"station_ids": station_ids}
            )

    return {"enriched": enriched, "objects_mapped": objects_mapped, "failed": failed}


def start_station_enricher(app, interval_s: int = 600):
    """Drain the enrichment queue periodically, in a background thread.

    Every worker starts one, but a pass only runs under an advisory lock: the queue is read
    without claiming rows, so concurrent passes would send the same batch.
    """
    def loop():
        time.sleep(60)
        while True:
            try:
                src.pg.init_db_engine()
                # A bare connection, since the drain opens pg_sessions and those cannot nest.
                # Transaction-scoped, so it is released however the pass ends.
                with app.app_context(), src.pg.pg_session_engine.begin() as lock_conn:
                    locked = lock_conn.execute(
                        text("SELECT pg_try_advisory_xact_lock(:key)"),
                        {"key": ENRICHER_LOCK_KEY},
                    ).scalar()
                    result = drain_enrichment_queue(max_batches=5) if locked else {}
                if result.get("enriched"):
                    logger.info(
                        f"Station enricher: {result['enriched']} station(s), "
                        f"{result['objects_mapped']} OSM object(s)."
                    )
            except Exception as e:
                # The queue is durable, so the next pass retries.
                logger.warning(f"Station enricher pass failed: {e}")
            time.sleep(interval_s)

    threading.Thread(target=loop, daemon=True, name="station-enricher").start()


def drain_enrichment_queue(
    max_batches: int | None = None, map_objects: bool = True, pg_session_=None
) -> dict:
    """Enrich stations awaiting it, in batches, up to `max_batches`.

    A failed batch is left queued for the next run.
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
            logger.warning(f"Enrichment batch failed, leaving it queued: {e}")
            totals["failed_batches"] += 1
            break

        totals["batches"] += 1
        time.sleep(PAUSE_BETWEEN_BATCHES_S)

    return totals
