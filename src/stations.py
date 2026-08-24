"""The station registry: write and maintenance side.

Trip endpoints stay free text; `station_labels` caches what each distinct *spelling*
resolves to. Keyed on the label rather than the trip, so a trip edit needs no sync —
see migration 0059. Identity is physical (wikidata, then uic_ref, then the OSM object),
never lexical: see migration 0058 for why, and 0060 for the ambiguity rule.

The cache is derived — check_labels_consistency() detects drift, rebuild_labels() cures it.
Reading happens in SQL: see migration 0060 and stats_stations.sql.
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

# Trip types the registry does not track. Canonical statement of this rule; SQL keeps its own
# copy in station_type_tracked() (migration 0060) and check_labels_consistency() compares them.
#
# Air already has a better register: `airports` is keyed on IATA, and an air label carries that
# code — "🇳🇱 Amsterdam Airport Schiphol (AMS)" — which src/api/stats.py parses back out.
# Resolving flights here would trade a stable identity for a Photon guess.
#
# The rest are private by nature: these endpoints are home addresses, friends' houses, hotels
# and restaurants. Nothing to canonicalise (two users writing "home" mean different places),
# and the admin queue displays sample_label verbatim ordered by use, which would list the most
# visited private addresses on the site. A privacy boundary, not a tuning decision — enforced
# here, in SQL, and by deleting what was already collected (migration 0061).
#
# `other` is the unknowable catch-all, so it is treated as private. Ski, aerialway, funicular
# and helicopter stay tracked: lift stations and helipads are public infrastructure.
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
    """The pool of places a trip type resolves against — its own mode, kept separate.

    Modes stay apart because their stops are different places: the bus stop at Amsterdam
    Centraal is not the tram stop, the train station or the metro beneath it. Splitting too
    finely is the safe direction — two rows for one place is one merge in the admin panel,
    one row for two places credits a trip to somewhere the user never went.

    Both spellings of accommodation map to one pool; the codebase carries
    TripTypes.ACCOMODATION = "accomodation" alongside "accommodation".
    """
    if trip_type in ("accommodation", "accomodation"):
        return "accommodation"
    return trip_type or "other"


# Shortest query the registry search will run. A trigram index cannot serve a needle with no
# trigrams in it, so anything shorter is a full scan of station_aliases.
MIN_SEARCH_LENGTH = 3


def search_registry(
    query: str, trip_type: str, user_id: int | None = None, limit: int = 10, pg_session_=None
) -> list[dict]:
    """Stations in the registry matching `query`, best first.

    Untracked trip types return nothing without querying, so the registry stays out of the
    autocomplete for personal modes by construction rather than by their pools being empty.
    """
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
                # -1 matches no user, so the visits boost is simply zero when anonymous.
                "user_id": user_id if user_id is not None else -1,
                "limit": limit,
            },
        ).fetchall()
    return [dict(row._mapping) for row in rows]


# How a user wants stations named. Mirrors User.station_display.
DISPLAY_MODES = ("international", "native", "language")


def display_name(station: dict, mode: str = "international", user_lang: str | None = None):
    """The name to show for a station, given a user's display preference.

    `station` is a row (or dict) carrying curated_name, name_intl, name_local and `names`.
    curated_name always wins — it is the admin's answer for cases the rules cannot settle,
    so a display mode must not route around it. 'language' falls back to the international
    name: OSM carries only ~2.4 name:* tags per station.

    Mirrored by station_display_name() in migration 0060 — keep the two in step.
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
        # Exact tag first, then the base language: a user set to pt-BR should still get
        # name:pt, and zh-Hans should fall back to name:zh.
        for key in (f"name:{user_lang}", f"name:{str(user_lang).split('-')[0]}"):
            value = names.get(key)
            if value:
                return value

    return international


def stations_for_osm_objects(pairs, pg_session_=None) -> dict:
    """Map (osm_type, osm_id) pairs to the station they belong to.

    `pairs` is an iterable of (osm_type, osm_id). Missing keys mean the object is not known
    to belong to any registered station. This is what makes deduplication exact — see
    station_osm_objects in migration 0058.
    """
    pairs = [(t, int(i)) for t, i in pairs if t and i is not None]
    if not pairs:
        return {}

    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            """
            SELECT o.osm_type, o.osm_id, COALESCE(s.superseded_by, s.station_id) AS station_id
            FROM station_osm_objects o
            JOIN stations s ON s.station_id = o.station_id
            WHERE (o.osm_type, o.osm_id) IN (
                SELECT x.t, x.i FROM unnest(CAST(:types AS text[]), CAST(:ids AS bigint[]))
                     AS x(t, i)
            )
            """,
            {"types": [t for t, _ in pairs], "ids": [i for _, i in pairs]},
        ).fetchall()
    return {(row["osm_type"], row["osm_id"]): row["station_id"] for row in rows}


def register_labels(labels, pg_session_=None) -> int:
    """Make sure these (label, trip_type) pairs exist in the cache, and resolve them.

    `labels` is an iterable of (raw_label, trip_type). Counts are left to
    refresh_label_counts(); see migration 0059 for why they are not maintained per write.
    """
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
        # Unscoped: one UPDATE over distinct spellings, not over the trips.
        pg.execute(resolve_station_labels_query(scoped=False))
    return len(rows)


def sync_trip_labels(trip_ids, pg_session_=None) -> None:
    """Register the labels of these trips. Accepts one id or a collection.

    Called wherever a trip's endpoints are written. Nothing is keyed on the trip, so this
    only ensures the spellings it uses are known; a later edit needs no re-sync.
    """
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
    """Re-derive every label from the trips and resolve them. The recovery path."""
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
        # A spelling whose last trip was deleted has no endpoints left, so the join above
        # cannot reach it and it would keep its old count at the top of the queue forever.
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

    Scoped to labels pointing here or matching a current alias — a handful of rows. Returns
    the number of trip endpoints now resolving here.
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
    """Detect drift in the derived cache. A full table pass — an admin diagnostic.

      missing       spellings in use that the cache does not know about
      misresolved   rows whose station_id disagrees with resolving them again now
      type_mismatch trip types where REGISTRY_EXCLUDED_TYPES and station_type_tracked()
                    disagree about whether the registry tracks them
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
        # Must call station_resolve_alias(): comparing station_labels against a CTE that
        # reads station_labels subtracts the table from itself and always reports zero.
        misresolved = pg.execute(
            """
            SELECT count(*) FROM station_labels sl
            WHERE sl.station_id IS DISTINCT FROM station_resolve_alias(
                sl.normalized, sl.station_type, station_flag_country(sl.sample_label)
            )
            """
        ).scalar()
        # Checked against the trip types actually in use, not a hardcoded list that would
        # itself need keeping in step.
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

    Matched by strength of identity, not by what the caller passed: a known OSM object
    first, then wikidata, then uic_ref, then the object itself. The new row is left with
    enriched_at NULL — the enrichment queue — so a trip save never waits on a third party.

    Every lookup resolves through superseded_by. A merged-away station keeps its anchors
    (migration 0062), so matching one must land on its survivor and never on the husk.
    """
    bucket = station_bucket(station_type)

    with get_or_create_pg_session(pg_session_) as pg:
        if osm_id is not None and osm_type:
            found = pg.execute(
                "SELECT COALESCE(s.superseded_by, s.station_id)"
                " FROM station_osm_objects o"
                " JOIN stations s ON s.station_id = o.station_id"
                " WHERE o.osm_type = :osm_type AND o.osm_id = :osm_id",
                {"osm_type": osm_type, "osm_id": osm_id},
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
            found = pg.execute(
                "SELECT COALESCE(superseded_by, station_id) FROM stations"
                " WHERE osm_type = :osm_type AND osm_id = :osm_id"
                " ORDER BY superseded_by NULLS FIRST LIMIT 1",
                {"osm_type": osm_type, "osm_id": osm_id},
            ).scalar()
            if found:
                return found

        if not name_intl:
            return None

        # The lookups above are a find-then-insert, so two concurrent saves through the same
        # new station both miss and the second would violate stations_osm_key.
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
            # Lost the race; the other transaction's row is as good as ours would have been.
            station_id = pg.execute(
                "SELECT station_id FROM stations"
                " WHERE osm_type = :osm_type AND osm_id = :osm_id",
                {"osm_type": osm_type, "osm_id": osm_id},
            ).scalar()
            if station_id is None:
                return None
            return station_id

        if osm_id is not None and osm_type:
            pg.execute(
                "INSERT INTO station_osm_objects (osm_type, osm_id, station_id)"
                " VALUES (:osm_type, :osm_id, :station_id)"
                " ON CONFLICT (osm_type, osm_id) DO NOTHING",
                {"osm_type": osm_type, "osm_id": osm_id, "station_id": station_id},
            )

        add_aliases(
            station_id,
            [(name_intl, "intl", None), (name_local, "local", None)],
            pg_session_=pg,
        )
        return station_id


FLAG_PREFIX_RE = re.compile(r"^[\U0001F1E6-\U0001F1FF]{2}\s*")

# A flag emoji is two regional indicator symbols standing for 'A'..'Z'.
_REGIONAL_INDICATOR_A = 0x1F1E6


def country_from_flag(label: str | None) -> str | None:
    """The ISO country code a label's leading flag emoji stands for, or None.

    Mirrors station_flag_country() (migration 0058). A hint, never a filter: stored flags are
    demonstrably wrong — "Paris Gare du Nord" appears under 🇬🇭 and 🇬🇧 as well as 🇫🇷.
    """
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
    """The label without its leading flag emoji.

    The flag is presentation glued onto the stored string, not part of the name; storing it
    would make name_intl differ from the OSM name by two invisible characters.
    """
    return FLAG_PREFIX_RE.sub("", label or "").strip()


def seed_stations_from_trip(new_trip: dict, trip_type: str, pg_session_=None) -> dict:
    """Register the places this trip's endpoints refer to, and return their station ids.

    `new_trip["originStation"]` is the [coords, label] pair the browser sends, with an
    optional third element carrying the OSM identity of the picked result — see
    stationSearchAutocomplete in static/js/util.js. Older clients and the import paths send
    only two elements, which is handled below.
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

        # No OSM identity, no registry row: a row identified only by its name can never be
        # matched, so registering one would add a fresh duplicate on every save. Free text
        # becomes an unresolved station_labels row instead, which is the right home for it.
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

    Silently skips spellings the station already has, and those that normalise to nothing
    (a name with no alphanumerics would otherwise match everything). Returns the number of
    rows actually inserted.
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

    A spelling held by two stations resolves to neither, so callers check this before adding
    one rather than discovering afterwards that they made a label unresolvable.
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

    The source survives with `superseded_by` set and its objects and aliases move across.
    Reads follow superseded_by, so trips keep working before the resync finishes.
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
        # Move the spellings across, dropping any the target already holds.
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
        # Hand the survivor any identity anchor it lacks. Must follow the line above: the
        # source only leaves the identity indexes once it is superseded (migration 0062), and
        # while both rows are live they cannot hold the same anchor.
        #
        # Only where the target is empty: a target already holding a different QID means an
        # admin merged two things OSM considers distinct, which is their call to make.
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
    """Every mode present in the data, with how much of each is registered or still queued."""
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
    """Remove a station. Its spellings go back to the unresolved queue.

    For a mis-registration, where merging is not the answer because the row should not exist
    at all. Nothing about a trip changes. Returns the spellings freed and their uses.
    """
    with get_or_create_pg_session(pg_session_) as pg:
        freed = pg.execute(
            "SELECT COALESCE(array_agg(label_id), '{}') AS ids,"
            " count(*) AS labels, COALESCE(sum(occurrences), 0) AS uses"
            " FROM station_labels WHERE station_id = :id",
            {"id": station_id},
        ).fetchone()
        label_ids = list(freed["ids"] or [])

        # Also the labels this station was *blocking*: removing one of two stations sharing a
        # spelling makes it resolvable, and those labels never pointed here. Captured before
        # the delete, because the aliases cascade away with the station.
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

        # A station merged into this one points here via superseded_by, and that FK has no
        # ON DELETE — clearing it first makes those rows ordinary stations again.
        unmerged = pg.execute(
            "UPDATE stations SET superseded_by = NULL WHERE superseded_by = :id",
            {"id": station_id},
        ).rowcount

        # Aliases and objects cascade; station_labels.station_id is ON DELETE SET NULL.
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
    """Where the trips using this label actually begin or end, or None if none have a path.

    The trip paths are the only record of where a written label meant, so this is what makes
    registering it a check rather than a guess. See src/sql/stations/label_location.sql for
    why the centre is a median and what `spread_m` is for.
    """
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
    pg_session_=None,
) -> list[dict]:
    """The admin work queue: spellings resolving to no station, costliest first.

    `search` filters diacritic-insensitively, so "munchen" finds "München". Filtered in SQL
    because the queue is ~143k rows and a page holds a couple of hundred.
    """
    search = (search or "").strip()
    with get_or_create_pg_session(pg_session_) as pg:
        rows = pg.execute(
            """
            SELECT sample_label AS raw_name,
                   station_type,
                   occurrences,
                   users,
                   station_flag_country(sample_label) AS country
            FROM station_labels
            WHERE station_id IS NULL AND occurrences > 0
              AND (:search = '' OR station_fold(sample_label) LIKE '%' || station_fold(:search) || '%')
              AND (:mode = '' OR station_type = :mode)
            ORDER BY occurrences DESC, label_id
            LIMIT :limit OFFSET :offset
            """,
            {"limit": limit, "search": search, "offset": offset, "mode": mode or ""},
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def count_unresolved(
    search: str | None = None, mode: str | None = None, pg_session_=None
) -> int:
    """How many unresolved spellings match `search` — not how many are being shown."""
    search = (search or "").strip()
    with get_or_create_pg_session(pg_session_) as pg:
        return pg.execute(
            """
            SELECT count(*) FROM station_labels
            WHERE station_id IS NULL AND occurrences > 0
              AND (:search = '' OR station_fold(sample_label) LIKE '%' || station_fold(:search) || '%')
              AND (:mode = '' OR station_type = :mode)
            """,
            {"search": search, "mode": mode or ""},
        ).scalar()


def registry_stats(pg_session_=None) -> dict:
    """Coverage figures for the admin panel.

    `endpoints` and `resolved` sum the cached per-label counts rather than scanning 1.5M
    trip rows on every load.
    """
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
