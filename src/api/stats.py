import json
import logging

from flask import (
    Blueprint,
    session,
)

from src.pg import pg_session
from src.sql import stats as stats_sql
from src.utils import get_user_id, lang

logger = logging.getLogger(__name__)

stats_blueprint = Blueprint("stats", __name__)

METRIC_NAMES = ["Trips", "Km", "Duration", "CO2"]

# Auto-generate the column keys for each metric
DEFAULT_METRICS = {
    m: (f"past{m}", f"plannedFuture{m}", f"future{m}") for m in METRIC_NAMES
}

METRIC_TO_DB_COLUMN = {
    "Km": "trip_length",
    "Duration": "trip_duration",
    "CO2": "carbon",
}


def _safe_get(d, key, default=0):
    return d.get(key, default) if isinstance(d, dict) else default


def get_stats_countries(pg, user_id, trip_type, year=None):
    result = pg.execute(
        stats_sql.stats_countries(),
        {"user_id": user_id, "tripType": trip_type, "year": year},
    ).fetchall()

    countries = {}

    for row in result:
        row_dict = dict(row._mapping)

        try:
            # The JSON object mapping country codes to distances.
            country_distances = json.loads(row_dict["countries"])
        except (json.JSONDecodeError, TypeError):
            continue

        # Total distance from the main trip record, used for proportions.
        total_trip_km = _safe_get(row_dict, "trip_length", 0)
        is_past = _safe_get(row_dict, "past", 0) != 0
        is_planned_future = _safe_get(row_dict, "plannedFuture", 0) != 0

        if not is_past and not is_planned_future:
            continue

        for country_code, country_km_data in country_distances.items():
            # Initialize the dictionary for a country if it's the first time we see it.
            # It will create keys like 'pastTrips', 'plannedFutureTrips', 'pastKm', etc.
            countries.setdefault(
                country_code,
                {col: 0 for m in METRIC_NAMES for col in DEFAULT_METRICS[m]},
            )

            # Handle cases where distance can be a simple number or a dict of values.
            country_km = (
                sum(country_km_data.values())
                if isinstance(country_km_data, dict)
                else country_km_data
            )

            # --- Metric-agnostic value calculation ---
            for metric in METRIC_NAMES:
                past_key, planned_future_key, _ = DEFAULT_METRICS[metric]
                value_to_add = 0

                if metric == "Trips":
                    # Trips are counted as 1 for each country in the trip.
                    value_to_add = 1
                elif metric == "Km":
                    # Km is the specific distance traveled in that country.
                    value_to_add = country_km
                else:
                    # Proportional split for all other metrics (e.g., Duration).
                    db_column = METRIC_TO_DB_COLUMN.get(metric)
                    if not db_column:
                        continue

                    total_metric_value = _safe_get(row_dict, db_column, 0)
                    if total_trip_km > 0:
                        proportion = country_km / total_trip_km
                        value_to_add = total_metric_value * proportion

                # Assign the calculated value to the correct time bucket (past or plannedFuture).
                if is_past:
                    countries[country_code][past_key] += value_to_add
                elif is_planned_future:
                    countries[country_code][planned_future_key] += value_to_add

    # Calculate total trips for sorting purposes. This is done after all rows are processed.
    for country_code, stats in countries.items():
        stats["totalTrips"] = stats.get("pastTrips", 0) + stats.get(
            "plannedFutureTrips", 0
        )

    # Sort countries by the calculated total trips, descending.
    sorted_countries = dict(
        sorted(
            countries.items(),
            key=lambda item: item[1].get("totalTrips", 0),
            reverse=True,
        )
    )

    # Convert to the required list format, ensuring backward compatibility of the output.
    countries_list = []
    for country, stats in sorted_countries.items():
        country_data = {"country": country}
        for metric in METRIC_NAMES:
            past_key, planned_future_key, _ = DEFAULT_METRICS[metric]
            country_data[past_key] = _safe_get(stats, past_key)
            country_data[planned_future_key] = _safe_get(stats, planned_future_key)
        countries_list.append(country_data)

    return countries_list


def get_stats_years(
    pg, user_id, lang, trip_type, year=None, metrics_map=DEFAULT_METRICS
):
    """Process year statistics with gap filling; supports dynamic metrics (Trips, Km, Duration, …)."""
    years = []
    years_temp = {}

    result = pg.execute(
        stats_sql.stats_year(),
        {"user_id": user_id, "tripType": trip_type, "year": year},
    ).fetchall()

    if not result:
        return ""

    result_list = [dict(row._mapping) for row in result]

    # separate "future" pseudo-year if present
    future = next((y for y in result_list if y.get("year") == "future"), None)
    result_list = [y for y in result_list if y.get("year") != "future"]

    if not result_list:
        if future:
            # Build future-only row from whatever metrics exist
            entry = {"year": lang.get("future", "Future")}
            # fill 0s for counts first
            entry["pastTrips"] = 0
            entry["plannedFutureTrips"] = 0
            entry["futureTrips"] = int(
                _safe_get(future, "futureTrips", _safe_get(future, "futuretrips", 0))
            )
            # include any metrics the SQL provided
            for metric_name, (past_key, planned_key, future_key) in metrics_map.items():
                # counts already covered above
                if metric_name == "Trips":
                    continue
                entry[past_key] = int(_safe_get(future, past_key, 0))
                entry[planned_key] = int(_safe_get(future, planned_key, 0))
                entry[future_key] = int(_safe_get(future, future_key, 0))
            return [entry]
        return ""

    # Build temp storage for all years and all metrics
    for year_row in result_list:
        y = int(year_row["year"])
        years_temp[y] = {}
        # Always include Trips
        years_temp[y]["pastTrips"] = int(_safe_get(year_row, "pastTrips", 0))
        years_temp[y]["plannedFutureTrips"] = int(
            _safe_get(year_row, "plannedFutureTrips", 0)
        )
        years_temp[y]["futureTrips"] = int(_safe_get(year_row, "futureTrips", 0))
        # Include any present metrics (Km, Duration, …)
        for metric_name, (past_key, planned_key, future_key) in metrics_map.items():
            if metric_name == "Trips":
                continue
            years_temp[y][past_key] = int(_safe_get(year_row, past_key, 0))
            years_temp[y][planned_key] = int(_safe_get(year_row, planned_key, 0))
            years_temp[y][future_key] = int(_safe_get(year_row, future_key, 0))

    # Fill gaps from first..last year
    first_year = int(result_list[0]["year"])
    last_year = int(result_list[-1]["year"])

    for year_num in range(first_year, last_year + 1):
        if year_num in years_temp:
            entry = {"year": year_num}
            entry["pastTrips"] = years_temp[year_num]["pastTrips"]
            entry["plannedFutureTrips"] = years_temp[year_num]["plannedFutureTrips"]
            entry["futureTrips"] = years_temp[year_num]["futureTrips"]
            # include all metrics that exist in this year row (keys present)
            for metric_name, (past_key, planned_key, future_key) in metrics_map.items():
                if metric_name == "Trips":
                    continue
                entry[past_key] = years_temp[year_num].get(past_key, 0)
                entry[planned_key] = years_temp[year_num].get(planned_key, 0)
                entry[future_key] = years_temp[year_num].get(future_key, 0)
            years.append(entry)
        else:
            # fully zeroed row
            entry = {
                "year": year_num,
                "pastTrips": 0,
                "plannedFutureTrips": 0,
                "futureTrips": 0,
            }
            for metric_name, (past_key, planned_key, future_key) in metrics_map.items():
                if metric_name == "Trips":
                    continue
                entry[past_key] = 0
                entry[planned_key] = 0
                entry[future_key] = 0
            years.append(entry)

    # Append "future" bucket if exists
    if future:
        entry = {
            "year": lang.get("future", "Future"),
            "pastTrips": 0,
            "plannedFutureTrips": 0,
        }
        entry["futureTrips"] = int(
            _safe_get(future, "futureTrips", _safe_get(future, "futuretrips", 0))
        )
        for metric_name, (past_key, planned_key, future_key) in metrics_map.items():
            if metric_name == "Trips":
                continue
            entry[past_key] = int(_safe_get(future, past_key, 0))
            entry[planned_key] = int(_safe_get(future, planned_key, 0))
            entry[future_key] = int(_safe_get(future, future_key, 0))
        years.append(entry)

    return years


def get_stats_general(pg, query_func, user_id, stat_name, trip_type, year=None):
    """
    Generic stats fetcher for operators, material, routes, stations
    Now returns both Trips and Km data in unified format
    """
    result = pg.execute(
        query_func(), {"user_id": user_id, "tripType": trip_type, "year": year}
    ).fetchall()

    stats = []
    for row in result:
        row_dict = dict(row._mapping)
        if row_dict.get(stat_name):
            stats.append(row_dict)
    return stats


def _collect_metric_fields(row_dict):
    """
    For a given SQL row (dict), return a flat dict containing
    past{Metric}, plannedFuture{Metric}, future{Metric} for every metric in DEFAULT_METRICS.
    Missing columns are defaulted to 0 so the payload is metric-agnostic.
    """
    payload = {}
    for m, (past_col, planned_col, future_col) in DEFAULT_METRICS.items():
        payload[f"past{m}"] = row_dict.get(past_col, 0)
        payload[f"plannedFuture{m}"] = row_dict.get(planned_col, 0)
        payload[f"future{m}"] = row_dict.get(future_col, 0)
    return payload


def get_stats_routes(pg, user_id, trip_type, year=None):
    """
    Process route statistics, metric-agnostic.
    Returns one object per route with generic metric fields:
    - past{Metric}, plannedFuture{Metric}, future{Metric} for each metric in DEFAULT_METRICS.
    Also includes "route" and "count" if available.
    """
    result = pg.execute(
        stats_sql.stats_routes(),
        {"user_id": user_id, "tripType": trip_type, "year": year},
    ).fetchall()

    routes = []
    for row in result:
        row_dict = dict(row._mapping)
        item = {
            "route": row_dict["route"],
            "count": row_dict.get("count", 0),
        }
        item.update(_collect_metric_fields(row_dict))
        routes.append(item)

    return routes


def get_stats_stations(pg, user_id, trip_type, year=None):
    """
    Process station statistics, metric-agnostic.
    Returns one object per station with generic metric fields:
    - past{Metric}, plannedFuture{Metric}, future{Metric} for each metric in DEFAULT_METRICS.
    Also includes "station" and "count" if available.
    """
    result = pg.execute(
        stats_sql.stats_stations(),
        {"user_id": user_id, "tripType": trip_type, "year": year},
    ).fetchall()

    stations = []
    for row in result:
        row_dict = dict(row._mapping)
        item = {
            "station": row_dict["station"],
            "count": row_dict.get("count", 0),
        }
        item.update(_collect_metric_fields(row_dict))
        stations.append(item)

    return stations


def fetch_stats(username, trip_type, year=None):
    """
    Fetch all statistics (both trips and km) in a single call
    If username is None, fetch stats for all users (admin mode)
    """
    stats = {}

    # Handle admin case - use None as user_id to get all users
    user_id = None if username is None else get_user_id(username)

    with pg_session() as pg:
        # Check if trip type is available for user (or any user if admin)
        available_types = pg.execute(
            stats_sql.type_available(), {"user_id": user_id}
        ).fetchall()

        type_exists = any(row[0] == trip_type for row in available_types)

        if not type_exists:
            return stats

        user_lang = session.get("userinfo", {}).get("lang", "en")
        lang_dict = lang.get(user_lang, {})

        # Fetch all stats with combined queries
        stats["operators"] = get_stats_general(
            pg=pg,
            query_func=stats_sql.stats_operator,
            user_id=user_id,
            stat_name="operator",
            trip_type=trip_type,
            year=year,
        )

        stats["material"] = get_stats_general(
            pg=pg,
            query_func=stats_sql.stats_material,
            user_id=user_id,
            stat_name="material",
            trip_type=trip_type,
            year=year,
        )

        stats["countries"] = get_stats_countries(
            pg=pg,
            user_id=user_id,
            trip_type=trip_type,
            year=year,
        )

        stats["years"] = get_stats_years(
            pg=pg,
            user_id=user_id,
            lang=lang_dict,
            trip_type=trip_type,
            year=year,
        )

        # Updated to use new functions
        stats["routes"] = get_stats_routes(
            pg=pg,
            user_id=user_id,
            trip_type=trip_type,
            year=year,
        )

        stats["stations"] = get_stats_stations(
            pg=pg,
            user_id=user_id,
            trip_type=trip_type,
            year=year,
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
