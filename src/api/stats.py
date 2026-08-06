import json
import logging
import re

from flask import Blueprint

from src.pg import pg_session
from src.sql import stats as stats_sql
from src.utils import get_user_id

logger = logging.getLogger(__name__)

stats_blueprint = Blueprint("stats", __name__)

# Every metric a chart can be plotted against. Each stats query emits
# past{Metric} and plannedFuture{Metric} for all of these, so adding one here
# means adding two columns per query and nothing else.
METRIC_NAMES = ["Trips", "Km", "Duration", "CO2", "Delay"]

# Column keys each metric occupies in a result row.
DEFAULT_METRICS = {m: (f"past{m}", f"plannedFuture{m}") for m in METRIC_NAMES}

# Category datasets: dimension name -> (query, identifier column). Every one of
# these groups trips by a single dimension and returns the same metric columns,
# so they all go through the same fetcher.
CATEGORY_DATASETS = {
    "operators": (stats_sql.stats_operator, "operator"),
    "material": (stats_sql.stats_material, "material"),
    "routes": (stats_sql.stats_routes, "route"),
    "stations": (stats_sql.stats_stations, "station"),
    "lines": (stats_sql.stats_lines, "line"),
    "vehicles": (stats_sql.stats_vehicles, "vehicle"),
    "trainsets": (stats_sql.stats_trainsets, "trainset"),
}

# Datasets `fetch_stats` produces when the caller doesn't ask for a subset.
ALL_DATASETS = tuple(CATEGORY_DATASETS) + ("countries", "years", "months")

# Where a metric's whole-trip total lives on a trip row, for metrics that get
# spread across the countries a trip crossed. Trips and Km are special-cased.
COUNTRY_SPLIT_COLUMNS = {
    "Duration": "trip_duration",
    "CO2": "carbon",
    "Delay": "arrival_delay",
}


def _rows_to_dicts(result):
    return [dict(row._mapping) for row in result]


# Decoration the charts hang off a row: the operator a line runs under, the code
# behind an airframe silhouette, a cached vessel photo, a country to flag.
_DECORATION_COLUMNS = frozenset(
    {"operator", "image_code", "image", "country", "units", "label"}
)

# What survives into the response. Everything else a query happens to select
# (the total* columns it sorts by, operator_id, long_name) is dropped — nothing
# reads them, and they ride along on every one of the thousands of rows a heavy
# user returns. The row limits are what actually shrank the payload; this just
# stops it growing back.
_METRIC_COLUMNS = (
    frozenset(key for keys in DEFAULT_METRICS.values() for key in keys)
    | _DECORATION_COLUMNS
    | {"count"}
)


def get_stats_category(pg, query_func, id_column, user_id, trip_type, year=None):
    """
    Fetch one category dataset (operators, material, lines, …).

    Every category query already returns the full metric set, so there is nothing
    to compute here: rows go out as they come back, minus the ones whose
    identifier is empty (a trip with no operator shouldn't become a blank bar).
    """
    result = pg.execute(
        query_func(), {"user_id": user_id, "tripType": trip_type, "year": year}
    ).fetchall()

    keep = _METRIC_COLUMNS | {id_column}
    return [
        {
            key: value
            for key, value in row.items()
            # Decoration that came back empty is dropped rather than sent as a
            # null: a ferry has no aircraft type, and 1000 rows of
            # "image_code": null is pure payload.
            if key in keep and (value is not None or key not in _DECORATION_COLUMNS)
        }
        for row in _rows_to_dicts(result)
        if row.get(id_column)
    ]


# Stations are stored as "🇫🇷 Charles de Gaulle International Airport (CDG)":
# a flag, a name, and — on airports — the code in trailing parentheses.
_STATION_CODE_RE = re.compile(r"\(([^()]+)\)\s*$")


def _station_codes(stats):
    """Every trailing parenthesised code in the station and route datasets."""
    codes = set()

    for row in stats.get("stations") or []:
        match = _STATION_CODE_RE.search(row.get("station") or "")
        if match:
            codes.add(match.group(1))

    for row in stats.get("routes") or []:
        try:
            endpoints = json.loads(row.get("route") or "[]")
        except ValueError:
            continue
        for endpoint in endpoints:
            match = _STATION_CODE_RE.search(endpoint or "")
            if match:
                codes.add(match.group(1))

    return codes


def get_airport_cities(pg, stats):
    """
    IATA code -> city, for the airports this payload mentions.

    The charts have room for a word, and the stored name doesn't hold one worth
    showing: cutting it at the first word gives "Charles (CDG)", "John (JFK)",
    "Logan (BOS)". The city is a column on `airports`, so look it up — one
    query over the codes actually charted, not a table the browser downloads.
    """
    codes = sorted(_station_codes(stats))
    if not codes:
        return {}

    result = pg.execute(stats_sql.airport_cities(), {"codes": codes}).fetchall()
    return {row[0]: row[1] for row in result}


def attach_vessel_photos(pg, vehicles):
    """
    Give each ferry row its cached vessel photo and ensign, if there are any.

    Reads the database directly and only. The /getVesselPhoto endpoint must not
    be used here: on a cache miss it runs a Google Images search and downloads
    the result, so pointing a chart at it would fire a search per bar per page
    load. A vessel with no cached photo simply gets none.

    Rows arrive keyed by vessel_display_name(reg, NULL) — the hull's CURRENT name
    where it is known, and the folded `reg` where it is not — so the lookup is
    built the same way round: one entry per hull under its current name, plus every
    key a bar might still be showing instead (its IMO, its synthetic id, and the
    MMSI of any registration) for the ships no name has been recorded for.
    """
    cached = {}
    for row in pg.execute(
        """
        SELECT v.imo, v.trainlog_id, cur.name, cur.country_code,
               p.local_image_path,
               ARRAY(SELECT a.mmsi FROM vessel_registrations a
                     WHERE a.vessel_id = v.uid AND a.mmsi IS NOT NULL) AS mmsis
        FROM vessels v
        -- The hull as it is now: that is what the chart is labelled with.
        LEFT JOIN vessel_registrations cur ON cur.uid = vessel_identity(v.uid, NULL)
        -- Its newest photo, from any registration — a bar showing one ship is better
        -- served by an older picture of it than by none.
        LEFT JOIN LATERAL (
            SELECT sp.local_image_path
            FROM ship_pictures sp
            JOIN vessel_registrations a ON a.uid = sp.registration_id
            WHERE a.vessel_id = v.uid AND sp.local_image_path IS NOT NULL
            ORDER BY (a.uid = cur.uid) DESC, sp.fetch_date DESC NULLS LAST, sp.uid DESC
            LIMIT 1
        ) p ON TRUE
        """
    ).fetchall():
        entry = (row["local_image_path"], row["country_code"])
        name = (row["name"] or "").strip()
        for key in [name.upper(), row["imo"], row["trainlog_id"], *(row["mmsis"] or [])]:
            if key:
                cached.setdefault(key, entry)

    for vehicle in vehicles:
        path, country = cached.get(vehicle["vehicle"].strip().upper(), (None, None))
        if path:
            vehicle["image"] = f"/static/images/ship_pictures/{path}"
        if country:
            vehicle["country"] = country


# How many trainset rows get their wagons resolved. The chart draws ten and the
# fullscreen view scrolls twenty at a time, so this is a few screens' depth; the
# tail keeps its numbers and simply has no picture.
TRAINSET_IMAGE_ROWS = 40

# Only the fields the strip needs to draw. The wagons table carries a lot more
# (era, notes, licence) and none of it belongs in a chart payload.
_WAGON_DISPLAY_FIELDS = ("image", "image_type", "image_ext", "px_per_meter", "label")


def _trainset_refs(value):
    """
    Read one material_type_advanced value.

    Returns (names, inline_units): a saved trainset is referenced by name (or
    several, for a composite), while an ad-hoc consist is stored inline as a
    list of units. A value that is JSON but neither shape yields nothing — it
    must not be mistaken for a trainset called "{...}".
    """
    if not value or not value.strip():
        return [], []

    stripped = value.strip()
    if not stripped.startswith(("[", "{")):
        # Not JSON at all: the plain name of a saved trainset.
        return [stripped], []

    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return [], []

    if isinstance(parsed, list):
        return [], [u for u in parsed if isinstance(u, dict) and u.get("name")]
    if isinstance(parsed, dict) and isinstance(parsed.get("trainsets"), list):
        return [str(n) for n in parsed["trainsets"] if n], []
    return [], []


def _trainset_label(names, inline):
    """
    Name a consist without touching the database.

    Saved sets are named directly; an ad-hoc one borrows its units' labels,
    which are stored inline alongside the wagon names ("MF 01" rather than
    "MF01_VB_M"). Repeats collapse, so a rake of six identical cars reads as one
    name and not as the same word six times.
    """
    if names:
        return " + ".join(dict.fromkeys(names))

    labels = [unit.get("label") or unit.get("name") for unit in inline]
    return " + ".join(dict.fromkeys(label for label in labels if label)) or None


def attach_trainset_units(pg, rows, username):
    """
    Resolve each trainset row to the wagons it is made of, and merge rows that
    end up naming the same train.

    Deliberately batched. The obvious route — public_trainset_info per row — runs
    one query per row plus one per wagon inside it (_enrich_units), which is
    hundreds of round trips for a chart. This resolves every row in two queries
    total, whatever the row count.

    Returns the rows to chart, which is not the list passed in: several stored
    values can describe the same consist (a saved set and an ad-hoc copy of it),
    and two bars with the same name on one axis is not a chart.
    """
    if not rows:
        return rows

    parsed = {row["trainset"]: _trainset_refs(row["trainset"]) for row in rows}

    # 1) every named trainset in one go, preferring the user's own set over the
    #    admin one of the same name (same precedence as _units_by_name).
    resolved = rows[:TRAINSET_IMAGE_ROWS]
    wanted = {
        name for row in resolved for name in parsed[row["trainset"]][0]
    }
    by_name = {}
    if wanted:
        for name, units_json in pg.execute(
            """
            SELECT DISTINCT ON (name) name, units_json
            FROM trainsets
            WHERE name = ANY(:names)
              -- The all-users view (:username IS NULL) is owner-only and
              -- aggregates everybody, so it resolves against every trainset.
              -- Restricting it to the 177 public ones left the other 2289
              -- unresolvable, and a name that resolves to nothing has no car
              -- count to draw even a placeholder from.
              AND (:username IS NULL OR is_admin OR username = :username)
            ORDER BY name,
                     -- the viewer's own set wins, then the public one, then
                     -- whichever is oldest, so the pick is stable
                     (username = :username AND NOT is_admin) DESC,
                     is_admin DESC,
                     id
            """,
            {"names": list(wanted), "username": username},
        ).fetchall():
            try:
                by_name[name] = json.loads(units_json or "[]")
            except (json.JSONDecodeError, TypeError):
                by_name[name] = []

    def units_of(value):
        names, inline = parsed[value]
        return inline if inline else [u for n in names for u in by_name.get(n, [])]

    # 2) every wagon those units point at, also in one go.
    wagon_names = {
        u["name"] for row in resolved for u in units_of(row["trainset"]) if u.get("name")
    }
    wagons = {}
    if wagon_names:
        wagons = {
            wagon["name"]: wagon
            for wagon in _rows_to_dicts(
                pg.execute(
                    "SELECT name, label, image, image_type, image_ext, px_per_meter"
                    " FROM wagons WHERE name = ANY(:names)",
                    {"names": list(wagon_names)},
                )
            )
        }

    resolved_ids = {id(row) for row in resolved}
    for row in rows:
        names, inline = parsed[row["trainset"]]

        units = []
        if id(row) in resolved_ids:
            for unit in units_of(row["trainset"]):
                wagon = wagons.get(unit.get("name")) or {}
                slim = {
                    k: wagon[k] for k in _WAGON_DISPLAY_FIELDS if wagon.get(k) is not None
                }
                slim["_side"] = unit.get("_side", "L")
                # Kept even with no artwork, so the strip can stand a
                # placeholder in its place: an outline in the right position
                # still tells you how many cars the set has, which is more than
                # an empty box does. `_phType` picks which outline.
                if unit.get("_phType"):
                    slim["_phType"] = unit["_phType"]
                units.append(slim)
            if units:
                row["units"] = units

        # Named sets show their name; ad-hoc ones are named from their units.
        # Resolved wagons give the nicest labels, but the inline JSON already
        # carries one per unit, so even an unresolved row past the cut-off gets
        # a readable name — never the stored JSON.
        row["label"] = (
            " + ".join(dict.fromkeys(u["label"] for u in units if u.get("label")))
            if units and not names
            else None
        ) or _trainset_label(names, inline)

    return _merge_by_label(rows)


def _merge_by_label(rows):
    """Fold rows sharing a label into one, summing every metric."""
    merged = {}
    for row in rows:
        key = row.get("label") or row["trainset"]
        target = merged.get(key)
        if target is None:
            merged[key] = dict(row)
            continue
        for past_key, planned_key in DEFAULT_METRICS.values():
            target[past_key] = (target.get(past_key) or 0) + (row.get(past_key) or 0)
            target[planned_key] = (target.get(planned_key) or 0) + (row.get(planned_key) or 0)
        # Keep the artwork of whichever copy had some.
        if "units" not in target and "units" in row:
            target["units"] = row["units"]

    return sorted(
        merged.values(),
        key=lambda r: (r.get("pastTrips") or 0) + (r.get("plannedFutureTrips") or 0),
        reverse=True,
    )


def get_stats_countries(pg, user_id, trip_type, year=None):
    """
    Split each trip across the countries it crossed.

    `trips.countries` maps a country code to the distance covered in it (either a
    number, or an object of sub-distances to add up). A trip counts once towards
    every country it touched, contributes its real distance in each, and has its
    other metrics divided between them in proportion to that distance.
    """
    result = pg.execute(
        stats_sql.stats_countries(),
        {"user_id": user_id, "tripType": trip_type, "year": year},
    ).fetchall()

    countries = {}

    for row in result:
        trip = dict(row._mapping)

        try:
            country_distances = json.loads(trip["countries"])
        except (json.JSONDecodeError, TypeError):
            continue

        # Which bucket this trip's values land in. Project trips are already
        # excluded by the query, so a trip is one or the other.
        if trip.get("past"):
            bucket = 0
        elif trip.get("plannedFuture"):
            bucket = 1
        else:
            continue

        trip_length = trip.get("trip_length") or 0

        for country_code, distance in country_distances.items():
            stats = countries.setdefault(
                country_code, _zero_row(country=country_code)
            )
            country_km = (
                sum(distance.values()) if isinstance(distance, dict) else distance
            ) or 0
            share = country_km / trip_length if trip_length > 0 else 0

            for metric, keys in DEFAULT_METRICS.items():
                if metric == "Trips":
                    value = 1
                elif metric == "Km":
                    value = country_km
                else:
                    total = trip.get(COUNTRY_SPLIT_COLUMNS[metric]) or 0
                    value = total * share
                stats[keys[bucket]] += value

    return sorted(
        countries.values(),
        key=lambda c: c["pastTrips"] + c["plannedFutureTrips"],
        reverse=True,
    )


def _zero_row(**identifiers):
    """A row with every metric at zero, used to fill gaps in the time series."""
    row = dict(identifiers)
    for past_key, planned_key in DEFAULT_METRICS.values():
        row[past_key] = 0
        row[planned_key] = 0
    return row


def _time_series(pg, query_func, user_id, trip_type, year, key, span):
    """
    Build a gap-filled time series.

    `key` is the row field holding the bucket ("year" or "month") and `span`
    turns the buckets that exist into the full range that should be plotted, so
    a year with no trips still gets a zero bar instead of being skipped.
    """
    result = pg.execute(
        query_func(), {"user_id": user_id, "tripType": trip_type, "year": year}
    ).fetchall()

    if not result:
        return []

    by_bucket = {}
    for row in _rows_to_dicts(result):
        bucket = int(row[key])
        entry = {key: bucket}
        for past_key, planned_key in DEFAULT_METRICS.values():
            entry[past_key] = row.get(past_key) or 0
            entry[planned_key] = row.get(planned_key) or 0
        by_bucket[bucket] = entry

    return [by_bucket.get(bucket, _zero_row(**{key: bucket})) for bucket in span(by_bucket)]


def get_stats_years(pg, user_id, trip_type, year=None):
    """Trips per year, with empty years in between filled in."""
    return _time_series(
        pg,
        stats_sql.stats_year,
        user_id,
        trip_type,
        year,
        key="year",
        # min/max rather than the first and last row: the query has no ORDER BY,
        # so row order is whatever plan Postgres picks.
        span=lambda buckets: range(min(buckets), max(buckets) + 1),
    )


def get_stats_months(pg, user_id, trip_type, year):
    """Trips per month of a single year — always all twelve months."""
    months = _time_series(
        pg,
        stats_sql.stats_months,
        user_id,
        trip_type,
        year,
        key="month",
        span=lambda buckets: range(1, 13),
    )
    year_value = int(year) if year is not None else None
    for month in months:
        month["year"] = year_value
    return months


def fetch_stats(username, trip_type, year=None, datasets=ALL_DATASETS):
    """
    Fetch statistics for one trip type.

    If username is None, fetch stats for all users (admin mode). `datasets`
    limits the work to the dimensions the caller actually plots — every extra
    dataset is another full pass over the user's trips.
    """
    stats = {}

    # Handle admin case - use None as user_id to get all users
    user_id = None if username is None else get_user_id(username)

    with pg_session() as pg:
        # Check if trip type is available for user (or any user if admin)
        available_types = pg.execute(
            stats_sql.type_available(), {"user_id": user_id}
        ).fetchall()

        if not any(row[0] == trip_type for row in available_types):
            return stats

        for name in datasets:
            if name in CATEGORY_DATASETS:
                query_func, id_column = CATEGORY_DATASETS[name]
                # The vehicles dimension is registrations everywhere except at sea,
                # where it is ships and one ship answers to three different written
                # identifiers — see stats_vessels.
                if name == "vehicles" and trip_type == "ferry":
                    query_func = stats_sql.stats_vessels
                stats[name] = get_stats_category(
                    pg=pg,
                    query_func=query_func,
                    id_column=id_column,
                    user_id=user_id,
                    trip_type=trip_type,
                    year=year,
                )

        # A vessel's photo and ensign only exist server-side, in `vessels` and
        # its photo cache. Aircraft need nothing here: the browser derives
        # the flag from the tail number and fetches the photo itself.
        if trip_type == "ferry" and stats.get("vehicles"):
            attach_vessel_photos(pg, stats["vehicles"])

        # Runs for the all-users view too, where username is None. Naming needs
        # no database at all, and the wagon lookup degrades correctly on its
        # own: `username = NULL` never matches, so an aggregate over everybody
        # resolves against public trainsets only and cannot leak one person's
        # private consist into it. Skipping this outright left every row of the
        # admin chart nameless.
        # Only air rows carry an airport code. Everywhere else a trailing
        # parenthetical is part of the station name ("Paris (Gare de Lyon)"),
        # so there is nothing to look up.
        if trip_type in ("air", "helicopter"):
            cities = get_airport_cities(pg, stats)
            if cities:
                stats["airportCities"] = cities

        if stats.get("trainsets"):
            stats["trainsets"] = attach_trainset_units(
                pg, stats["trainsets"], username
            )

        if "countries" in datasets:
            stats["countries"] = get_stats_countries(
                pg=pg, user_id=user_id, trip_type=trip_type, year=year
            )

        if "years" in datasets:
            stats["years"] = get_stats_years(
                pg=pg, user_id=user_id, trip_type=trip_type, year=year
            )

        # Months only mean something once a year has been picked.
        if "months" in datasets and year:
            stats["months"] = get_stats_months(
                pg=pg, user_id=user_id, trip_type=trip_type, year=year
            )

    return stats


def get_distinct_stat_years(username, trip_type):
    """Get list of years with statistics available"""
    user_id = None if username is None else get_user_id(username)

    with pg_session() as pg:
        result = pg.execute(
            stats_sql.distinct_stat_years(), {"user_id": user_id, "tripType": trip_type}
        ).fetchall()
    return [row[0] for row in result]
