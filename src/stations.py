"""The station registry: write and maintenance side.

Trip endpoints stay free text; `station_labels` caches what each distinct spelling resolves
to, per mode. Identity is physical (wikidata, then uic_ref, then the OSM object), never the
name. The cache is derived: rebuild_labels() recreates it from the trips.
"""

import json
import logging
import re

from src.pg import get_or_create_pg_session
from src.sql.stations import (
    label_location_query,
    resolve_station_labels_query,
    search_stations_query,
)

logger = logging.getLogger(__name__)

# Trip types the registry does not track. Mirrored by station_type_tracked() in SQL.
#
# Air is already identified by IATA code. The rest are private: their endpoints are homes,
# hotels and restaurants, and the admin queue would list them by popularity.
REGISTRY_EXCLUDED_TYPES = frozenset(
    {
        "air",
        "car",
        "walk",
        "cycle",
        "scooter",
        "accommodation",
        "restaurant",
        "poi",
        "other",
    }
)


def tracks_stations(trip_type: str | None) -> bool:
    """Whether the registry resolves endpoints for this trip type."""
    return station_bucket(trip_type) not in REGISTRY_EXCLUDED_TYPES


def station_bucket(trip_type: str | None) -> str:
    """The pool of stations a trip type resolves against.

    Modes stay apart because their stops are different places: the bus stop at Amsterdam
    Centraal is not the train station.
    """
    if trip_type in ("accommodation", "accomodation"):
        return "accommodation"
    return trip_type or "other"


# A trigram index cannot serve a shorter needle.
MIN_SEARCH_LENGTH = 3


def search_registry(
    query: str, trip_type: str, user_id: int | None = None, limit: int = 10, pg_session_=None
) -> list[dict]:
    """Stations in the registry matching `query`, best first."""
    if not tracks_stations(trip_type):
        return []
    if not query or len(query.strip()) < MIN_SEARCH_LENGTH:
        return []

    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            search_stations_query(),
            {
                "query": query.strip(),
                "station_type": station_bucket(trip_type),
                "user_id": user_id if user_id is not None else -1,
                "limit": limit,
            },
        ).fetchall()
    return [dict(row._mapping) for row in rows]


DISPLAY_MODES = ("international", "native", "language")


def display_name(station: dict, mode: str = "international", user_lang: str | None = None):
    """The name to show for a station, given a user's display preference.

    curated_name always wins. Mirrored by station_display_name() in SQL.
    """
    if not station:
        return None

    curated = station.get("curated_name")
    if curated:
        return curated

    international = station.get("name_intl")
    names = station.get("names") or {}
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except ValueError:
            names = {}

    if mode == "native":
        return station.get("name_local") or international

    if mode == "language" and user_lang:
        # pt-BR falls back to name:pt.
        for key in (f"name:{user_lang}", f"name:{str(user_lang).split('-')[0]}"):
            value = names.get(key)
            if value:
                return value

    return international


def stations_for_osm_objects(pairs, station_type, pg_session_=None) -> dict:
    """Map (osm_type, osm_id) pairs to their station in this mode's pool."""
    pairs = [(t, int(i)) for t, i in pairs if t and i is not None]
    if not pairs:
        return {}

    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            """
            SELECT o.osm_type, o.osm_id, COALESCE(s.superseded_by, s.station_id) AS station_id
            FROM station_osm_objects o
            JOIN stations s ON s.station_id = o.station_id
            WHERE s.station_type = :station_type
              AND (o.osm_type, o.osm_id) IN (
                SELECT x.t, x.i FROM unnest(CAST(:types AS text[]), CAST(:ids AS bigint[]))
                     AS x(t, i)
            )
            """,
            {"types": [t for t, _ in pairs], "ids": [i for _, i in pairs],
             "station_type": station_bucket(station_type)},
        ).fetchall()
    return {(row["osm_type"], row["osm_id"]): row["station_id"] for row in rows}


def register_labels(labels, pg_session_=None) -> int:
    """Make sure these (label, trip_type) pairs exist in the cache, and resolve them."""
    rows = [
        {"label": raw.strip(), "station_type": station_bucket(trip_type)}
        for raw, trip_type in labels
        if raw and raw.strip() and tracks_stations(trip_type)
    ]
    if not rows:
        return 0

    with get_or_create_pg_session(pg_session_) as pg:
        for row in rows:
            pg.execute(
                """
                INSERT INTO station_labels (normalized, station_type, sample_label)
                SELECT station_normalize(:label), :station_type, :label
                WHERE station_normalize(:label) IS NOT NULL
                ON CONFLICT (station_type, normalized) DO NOTHING
                """,
                row,
            )
        pg.execute(resolve_station_labels_query(scoped=False))
    return len(rows)


def sync_trip_labels(trip_ids, pg_session_=None) -> None:
    """Register the labels of these trips. Accepts one id or a collection."""
    if not isinstance(trip_ids, (list, tuple, set)):
        trip_ids = [trip_ids]
    trip_ids = [int(t) for t in trip_ids]
    if not trip_ids:
        return

    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            "SELECT origin_station, destination_station, trip_type FROM trips"
            " WHERE trip_id = ANY(:trip_ids)",
            {"trip_ids": trip_ids},
        ).fetchall()
        labels = []
        for row in rows:
            labels.append((row["origin_station"], row["trip_type"]))
            labels.append((row["destination_station"], row["trip_type"]))
        register_labels(labels, pg_session_=pg)


def rebuild_labels(pg_session_=None) -> int:
    """Re-derive every label from the trips and resolve them."""
    with get_or_create_pg_session(pg_session_) as pg:
        pg.execute(
            """
            INSERT INTO station_labels (normalized, station_type, sample_label)
            SELECT DISTINCT ON (normalized, station_type) normalized, station_type, raw
            FROM trip_station_endpoints
            ON CONFLICT (station_type, normalized) DO NOTHING
            """
        )
        pg.execute(resolve_station_labels_query(scoped=False))
        refresh_label_counts(pg_session_=pg)
        return pg.execute("SELECT count(*) FROM station_labels").scalar()


def refresh_label_counts(pg_session_=None) -> int:
    """Recompute how many trip endpoints and users each spelling accounts for."""
    with get_or_create_pg_session(pg_session_) as pg:
        pg.execute(
            """
            UPDATE station_labels sl
            SET occurrences = c.n, users = c.u
            FROM (
                SELECT normalized, station_type,
                       count(*)::int AS n, count(DISTINCT user_id)::int AS u
                FROM trip_station_endpoints
                GROUP BY normalized, station_type
            ) c
            WHERE sl.normalized = c.normalized AND sl.station_type = c.station_type
              AND (sl.occurrences, sl.users) IS DISTINCT FROM (c.n, c.u)
            """
        )
        # The join above cannot reach spellings with no trips left.
        pg.execute(
            """
            UPDATE station_labels sl
            SET occurrences = 0, users = 0
            WHERE (sl.occurrences <> 0 OR sl.users <> 0)
              AND NOT EXISTS (
                  SELECT 1 FROM trip_station_endpoints e
                  WHERE e.normalized = sl.normalized
                    AND e.station_type = sl.station_type
              )
            """
        )
        return pg.execute("SELECT count(*) FROM station_labels").scalar()


def resync_station(station_id: int, pg_session_=None) -> int:
    """Re-resolve the spellings affected by a change to one station.

    Returns the number of trip endpoints now resolving to it.
    """
    with get_or_create_pg_session(pg_session_) as pg:
        pg.execute(
            resolve_station_labels_query(scoped=True), {"station_ids": [station_id]}
        )
        return pg.execute(
            "SELECT COALESCE(sum(occurrences), 0) FROM station_labels"
            " WHERE station_id = :id",
            {"id": station_id},
        ).scalar()


def check_labels_consistency(pg_session_=None) -> dict:
    """Detect drift in the derived cache. A full table pass.

      missing       spellings in use that the cache does not know about
      misresolved   rows whose station_id disagrees with resolving them again now
      type_mismatch trip types where Python and SQL disagree about tracking
    """
    with get_or_create_pg_session(pg_session_) as pg:
        missing = pg.execute(
            """
            SELECT count(*) FROM (
                SELECT DISTINCT normalized, station_type FROM trip_station_endpoints
            ) used
            WHERE NOT EXISTS (
                SELECT 1 FROM station_labels sl
                WHERE sl.normalized = used.normalized
                  AND sl.station_type = used.station_type
            )
            """
        ).scalar()
        # Must call station_resolve_alias(): a CTE reading station_labels would compare the table
        # with itself.
        misresolved = pg.execute(
            """
            SELECT count(*) FROM station_labels sl
            WHERE sl.station_id IS DISTINCT FROM station_resolve_alias(
                sl.normalized, sl.station_type, station_flag_country(sl.sample_label)
            )
            """
        ).scalar()
        type_mismatch = [
            row[0]
            for row in pg.execute(
                "SELECT DISTINCT trip_type, station_type_tracked(trip_type) FROM trips"
            ).fetchall()
            if bool(row[1]) is not tracks_stations(row[0])
        ]
    return {
        "missing": missing,
        "misresolved": misresolved,
        "type_mismatch": type_mismatch,
        "consistent": not (missing or misresolved or type_mismatch),
    }


def find_station(
    *,
    station_type: str,
    osm_type: str | None = None,
    osm_id: int | None = None,
    wikidata: str | None = None,
    uic_ref: str | None = None,
    pg_session_=None,
) -> int | None:
    """The existing station for this place, or None.

    Tries a known OSM object, then wikidata, then uic_ref, within the mode's pool, and
    follows superseded_by to the surviving station.
    """
    bucket = station_bucket(station_type)
    with get_or_create_pg_session(pg_session_) as pg:
        if osm_id is not None and osm_type:
            found = pg.execute(
                "SELECT COALESCE(s.superseded_by, s.station_id)"
                " FROM station_osm_objects o"
                " JOIN stations s ON s.station_id = o.station_id"
                " WHERE o.osm_type = :osm_type AND o.osm_id = :osm_id"
                "   AND s.station_type = :station_type",
                {"osm_type": osm_type, "osm_id": osm_id, "station_type": bucket},
            ).scalar()
            if found:
                return found

        for column, value in (("wikidata", wikidata), ("uic_ref", uic_ref)):
            if not value:
                continue
            found = pg.execute(
                f"SELECT COALESCE(superseded_by, station_id) FROM stations"
                f" WHERE {column} = :value AND station_type = :station_type"
                f" ORDER BY superseded_by NULLS FIRST LIMIT 1",
                {"value": value, "station_type": bucket},
            ).scalar()
            if found:
                return found

        if osm_id is not None and osm_type:
            return pg.execute(
                "SELECT COALESCE(superseded_by, station_id) FROM stations"
                " WHERE osm_type = :osm_type AND osm_id = :osm_id"
                "   AND station_type = :station_type"
                " ORDER BY superseded_by NULLS FIRST LIMIT 1",
                {"osm_type": osm_type, "osm_id": osm_id, "station_type": bucket},
            ).scalar()
    return None


def upsert_station(
    *,
    station_type: str,
    name_intl: str,
    name_local: str | None = None,
    osm_type: str | None = None,
    osm_id: int | None = None,
    wikidata: str | None = None,
    uic_ref: str | None = None,
    country_code: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    pg_session_=None,
) -> int | None:
    """Find or create the station for a place the user just picked. Returns its station_id.

    New rows are left for the background enricher, so a trip save never waits on OSM.
    """
    bucket = station_bucket(station_type)

    with get_or_create_pg_session(pg_session_) as pg:
        found = find_station(
            station_type=station_type,
            osm_type=osm_type,
            osm_id=osm_id,
            wikidata=wikidata,
            uic_ref=uic_ref,
            pg_session_=pg,
        )
        if found:
            return found

        if not name_intl:
            return None

        # Find-then-insert: a concurrent save of the same new station can win the race.
        station_id = pg.execute(
            """
            INSERT INTO stations (osm_type, osm_id, wikidata, uic_ref, station_type,
                                  name_local, name_intl, country_code, lat, lng)
            VALUES (:osm_type, :osm_id, :wikidata, :uic_ref, :station_type,
                    :name_local, :name_intl, :country_code, :lat, :lng)
            ON CONFLICT DO NOTHING
            RETURNING station_id
            """,
            {
                "osm_type": osm_type,
                "osm_id": osm_id,
                "wikidata": wikidata,
                "uic_ref": uic_ref,
                "station_type": bucket,
                "name_local": name_local,
                "name_intl": name_intl,
                "country_code": country_code,
                "lat": lat,
                "lng": lng,
            },
        ).scalar()

        if station_id is None:
            station_id = pg.execute(
                "SELECT station_id FROM stations"
                " WHERE osm_type = :osm_type AND osm_id = :osm_id"
                "   AND station_type = :station_type",
                {"osm_type": osm_type, "osm_id": osm_id, "station_type": bucket},
            ).scalar()
            if station_id is None:
                return None
            return station_id

        if osm_id is not None and osm_type:
            pg.execute(
                "INSERT INTO station_osm_objects (osm_type, osm_id, station_id)"
                " VALUES (:osm_type, :osm_id, :station_id)"
                " ON CONFLICT (osm_type, osm_id, station_id) DO NOTHING",
                {"osm_type": osm_type, "osm_id": osm_id, "station_id": station_id},
            )

        add_aliases(
            station_id,
            [(name_intl, "intl", None), (name_local, "local", None)],
            pg_session_=pg,
        )
        return station_id


FLAG_PREFIX_RE = re.compile(r"^[\U0001F1E6-\U0001F1FF]{2}\s*")

_REGIONAL_INDICATOR_A = 0x1F1E6


def country_from_flag(label: str | None) -> str | None:
    """The ISO country code a label's leading flag emoji stands for, or None."""
    if not label or len(label) < 2:
        return None
    a, b = ord(label[0]), ord(label[1])
    if not (
        _REGIONAL_INDICATOR_A <= a <= _REGIONAL_INDICATOR_A + 25
        and _REGIONAL_INDICATOR_A <= b <= _REGIONAL_INDICATOR_A + 25
    ):
        return None
    return chr(a - _REGIONAL_INDICATOR_A + 65) + chr(b - _REGIONAL_INDICATOR_A + 65)


def strip_flag(label: str | None) -> str:
    """The label without its leading flag emoji."""
    return FLAG_PREFIX_RE.sub("", label or "").strip()


def seed_stations_from_trip(new_trip: dict, trip_type: str, pg_session_=None) -> dict:
    """Register the stations this trip's endpoints were picked from.

    Each endpoint is [coords, label, osm_ref] as sent by stationSearchAutocomplete in util.js;
    imports and manual stations send no osm_ref.
    """
    result = {}
    if not tracks_stations(trip_type):
        return result

    for key in ("originStation", "destinationStation"):
        ref = new_trip.get(key)
        if not isinstance(ref, (list, tuple)) or len(ref) < 2:
            continue

        coords, label = ref[0], ref[1]
        osm_ref = ref[2] if len(ref) > 2 and isinstance(ref[2], dict) else {}

        name = strip_flag(label)
        if not name:
            continue

        # A row with no OSM identity could never be matched again; free text stays an
        # unresolved label instead.
        if not (osm_ref.get("osm_id") and osm_ref.get("osm_type")):
            continue

        lat = lng = None
        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
            try:
                lat, lng = float(coords[0]), float(coords[1])
            except (TypeError, ValueError):
                lat = lng = None

        try:
            result[key] = upsert_station(
                station_type=trip_type,
                name_intl=name,
                name_local=osm_ref.get("name_local"),
                osm_type=osm_ref.get("osm_type"),
                osm_id=osm_ref.get("osm_id"),
                country_code=country_from_flag(label),
                lat=lat,
                lng=lng,
                pg_session_=pg_session_,
            )
        except Exception as e:
            # Never a reason to fail a trip save.
            logger.warning(f"Could not register station {name!r}: {e}")
    return result


def add_aliases(station_id: int, aliases, pg_session_=None) -> int:
    """Record spellings for a station. `aliases` is an iterable of (alias, kind, lang).

    Returns the number of new rows.
    """
    rows = [
        {"station_id": station_id, "alias": alias.strip(), "kind": kind, "lang": lang}
        for alias, kind, lang in aliases
        if alias and alias.strip()
    ]
    if not rows:
        return 0

    inserted = 0
    with get_or_create_pg_session(pg_session_) as pg:
        for row in rows:
            result = pg.execute(
                """
                INSERT INTO station_aliases (station_id, alias, kind, lang)
                SELECT :station_id, :alias, :kind, :lang
                WHERE station_normalize(:alias) IS NOT NULL
                ON CONFLICT (station_id, normalized) DO NOTHING
                RETURNING alias_id
                """,
                row,
            ).fetchone()
            if result is not None:
                inserted += 1
    return inserted


def stations_holding_alias(alias: str, station_type: str, pg_session_=None) -> list[int]:
    """Which live stations in this pool already answer to this spelling.

    A spelling held by two stations resolves to neither.
    """
    if not alias or not alias.strip():
        return []
    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            """
            SELECT DISTINCT s.station_id
            FROM station_aliases a
            JOIN stations s ON s.station_id = a.station_id
            WHERE a.normalized = station_normalize(:alias)
              AND s.station_type = :station_type
              AND s.superseded_by IS NULL
            """,
            {"alias": alias.strip(), "station_type": station_bucket(station_type)},
        ).fetchall()
    return [row[0] for row in rows]


def merge_stations(source_id: int, target_id: int, pg_session_=None) -> dict:
    """Fold one station into another.

    The source is kept with superseded_by set; its objects and aliases move to the target.
    """
    with get_or_create_pg_session(pg_session_) as pg:
        same_pool = pg.execute(
            "SELECT (SELECT station_type FROM stations WHERE station_id = :s)"
            " = (SELECT station_type FROM stations WHERE station_id = :t)",
            {"s": source_id, "t": target_id},
        ).scalar()
        if not same_pool:
            return {"success": False, "error": "stations are in different pools"}

        pg.execute(
            "UPDATE station_osm_objects SET station_id = :t WHERE station_id = :s",
            {"s": source_id, "t": target_id},
        )
        pg.execute(
            """
            UPDATE station_aliases a SET station_id = :t, kind = 'alias'
            WHERE a.station_id = :s
              AND NOT EXISTS (
                  SELECT 1 FROM station_aliases b
                  WHERE b.station_id = :t AND b.normalized = a.normalized
              )
            """,
            {"s": source_id, "t": target_id},
        )
        pg.execute(
            "DELETE FROM station_aliases WHERE station_id = :s", {"s": source_id}
        )
        pg.execute(
            "UPDATE stations SET superseded_by = :t WHERE station_id = :s",
            {"s": source_id, "t": target_id},
        )
        # After the superseded_by update: two live rows cannot hold the same anchor. Only where
        # the target has none, as a different QID means the admin merged two distinct objects.
        pg.execute(
            """
            UPDATE stations t
            SET wikidata = COALESCE(t.wikidata, s.wikidata),
                uic_ref  = COALESCE(t.uic_ref, s.uic_ref)
            FROM stations s
            WHERE t.station_id = :t AND s.station_id = :s
            """,
            {"s": source_id, "t": target_id},
        )
        pg.execute(
            resolve_station_labels_query(scoped=True),
            {"station_ids": [source_id, target_id]},
        )
        moved = pg.execute(
            "SELECT COALESCE(sum(occurrences), 0) FROM station_labels"
            " WHERE station_id = :t",
            {"t": target_id},
        ).scalar()
    return {"success": True, "trips_resynced": moved}


def modes_in_use(pg_session_=None) -> list[dict]:
    """Every tracked mode, with its registered and unresolved counts."""
    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            """
            SELECT station_type,
                   count(*) FILTER (WHERE station_id IS NULL AND occurrences > 0)
                       AS unresolved,
                   COALESCE(sum(occurrences) FILTER (WHERE station_id IS NULL), 0)
                       AS unresolved_uses,
                   (SELECT count(*) FROM stations s
                    WHERE s.station_type = l.station_type) AS stations
            FROM station_labels l
            GROUP BY station_type
            ORDER BY sum(occurrences) DESC
            """
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def delete_station(station_id: int, pg_session_=None) -> dict:
    """Remove a station. Its spellings go back to the unresolved queue."""
    with get_or_create_pg_session(pg_session_) as pg:
        freed = pg.execute(
            "SELECT COALESCE(array_agg(label_id), '{}') AS ids,"
            " count(*) AS labels, COALESCE(sum(occurrences), 0) AS uses"
            " FROM station_labels WHERE station_id = :id",
            {"id": station_id},
        ).fetchone()
        label_ids = list(freed["ids"] or [])

        # Also the labels this station was blocking by sharing their spelling. Read before the
        # delete, since the aliases cascade away with it.
        label_ids += [
            row[0]
            for row in pg.execute(
                """
                SELECT l.label_id
                FROM station_labels l
                WHERE l.station_id IS NULL
                  AND l.normalized IN (
                      SELECT normalized FROM station_aliases WHERE station_id = :id
                  )
                """,
                {"id": station_id},
            ).fetchall()
        ]
        label_ids = list(dict.fromkeys(label_ids))

        # superseded_by has no ON DELETE; stations merged into this one become ordinary again.
        unmerged = pg.execute(
            "UPDATE stations SET superseded_by = NULL WHERE superseded_by = :id",
            {"id": station_id},
        ).rowcount

        deleted = pg.execute(
            "DELETE FROM stations WHERE station_id = :id", {"id": station_id}
        ).rowcount
        if not deleted:
            return {"success": False, "error": "no such station"}

        if label_ids:
            pg.execute(
                """
                UPDATE station_labels sl
                SET station_id = station_resolve_alias(
                    sl.normalized, sl.station_type,
                    station_flag_country(sl.sample_label)
                )
                WHERE sl.label_id = ANY(:label_ids)
                """,
                {"label_ids": label_ids},
            )

    return {
        "success": True,
        "labels_freed": freed["labels"],
        "uses_freed": int(freed["uses"]),
        "unmerged": unmerged,
    }


def label_location(label: str, station_type: str, pg_session_=None) -> dict | None:
    """Where the trips using this label begin or end, or None if none have a path."""
    if not label or not label.strip():
        return None
    with get_or_create_pg_session(pg_session_) as pg:
        row = pg.execute(
            label_location_query(),
            {"label": label.strip(), "station_type": station_bucket(station_type)},
        ).fetchone()
    if row is None or row["lat"] is None:
        return None
    return {
        "lat": float(row["lat"]),
        "lng": float(row["lng"]),
        "points": int(row["points"]),
        "spread_m": float(row["spread_m"]) if row["spread_m"] is not None else None,
    }


def unresolved_labels(
    limit: int = 100,
    search: str | None = None,
    offset: int = 0,
    mode: str | None = None,
    status: str | None = None,
    pg_session_=None,
) -> list[dict]:
    """The admin work queue: spellings resolving to no station, most used first.

    `status` filters on the seeding verdict; 'unchecked' means none yet.
    """
    search = (search or "").strip()
    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            """
            SELECT sample_label AS raw_name,
                   station_type,
                   occurrences,
                   users,
                   auto_result,
                   station_flag_country(sample_label) AS country
            FROM station_labels
            WHERE station_id IS NULL AND occurrences > 0
              AND (:search = '' OR station_fold(sample_label) LIKE '%' || station_fold(:search) || '%')
              AND (:mode = '' OR station_type = :mode)
              AND (:status = ''
                   OR (:status = 'unchecked' AND auto_result IS NULL)
                   OR auto_result = :status)
            ORDER BY occurrences DESC, label_id
            LIMIT :limit OFFSET :offset
            """,
            {"limit": limit, "search": search, "offset": offset,
             "mode": mode or "", "status": status or ""},
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def count_unresolved(
    search: str | None = None,
    mode: str | None = None,
    status: str | None = None,
    pg_session_=None,
) -> int:
    """How many unresolved spellings match the filters."""
    search = (search or "").strip()
    with get_or_create_pg_session(pg_session_) as pg:
        return pg.execute(
            """
            SELECT count(*) FROM station_labels
            WHERE station_id IS NULL AND occurrences > 0
              AND (:search = '' OR station_fold(sample_label) LIKE '%' || station_fold(:search) || '%')
              AND (:mode = '' OR station_type = :mode)
              AND (:status = ''
                   OR (:status = 'unchecked' AND auto_result IS NULL)
                   OR auto_result = :status)
            """,
            {"search": search, "mode": mode or "", "status": status or ""},
        ).scalar()


def registry_stats(pg_session_=None) -> dict:
    """Coverage figures for the admin panel."""
    with get_or_create_pg_session(pg_session_) as pg:
        return dict(
            pg.execute(
                """
                SELECT (SELECT count(*) FROM stations) AS stations,
                       (SELECT count(*) FROM stations WHERE enriched_at IS NULL)
                           AS awaiting_enrichment,
                       (SELECT count(*) FROM station_aliases) AS aliases,
                       (SELECT COALESCE(sum(occurrences), 0) FROM station_labels)
                           AS endpoints,
                       (SELECT COALESCE(sum(occurrences), 0) FROM station_labels
                        WHERE station_id IS NOT NULL) AS resolved,
                       (SELECT count(*) FROM station_labels WHERE station_id IS NULL)
                           AS unresolved_labels
                """
            ).fetchone()._mapping
        )
