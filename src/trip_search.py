"""The field filters of the trips smart search ("from:Oslo", "duration>2h").

The client sends them as a list rather than one value per column, so the same field
can carry several terms: they are ANDed, which is what makes "date:2020
date:!2020-02..2020-03" mean "2020 except February and March". Within one term, '|'
separates alternatives, which are ORed.
"""

import json

from src.operators import find_operator_ids
from src.search_terms import comparison_condition, has_wildcard, like_pattern

# The fields a filter may name, and the trip type filter that can be pushed down into
# the base CTE. Anything else is dropped: the names reach SQL, so the allowlist is
# what keeps them safe.
FILTER_FIELDS = {
    "type",
    "origin_station",
    "destination_station",
    "start_datetime",
    "trip_duration_seconds",
    "trip_length",
    "trip_speed",
    "operator",
    "line_name",
    "countries",
    "visibility",
    "material_type",
    "reg",
    "notes",
    "price",
}

# Prices are not shown on a public listing, so they must not be filtered on there
# either: a comparison would read a hidden value out one answer at a time.
PUBLIC_HIDDEN_FIELDS = {"price"}

# Station names are stored with the country flag in front of them ("🇳🇴 Oslo S"), which
# a substring match never notices but an exact or anchored one would fail on. Both are
# compared against the name alone, the same way the ordering sorts on it.
def _unflagged(column):
    return (
        f"CASE WHEN ascii({column}) BETWEEN 127462 AND 127487"
        f" THEN substring({column} FROM 4) ELSE {column} END"
    )


# Fields matched as plain text, with the expression each one matches against. The
# COALESCE ones are nullable; a bare column never is.
_TEXT_FIELDS = {
    "type": "type",
    "origin_station": _unflagged("origin_station"),
    "destination_station": _unflagged("destination_station"),
    "countries": "countries",
    "visibility": "visibility",
    "line_name": "COALESCE(line_name, '')",
    "reg": "COALESCE(reg, '')",
    "notes": "COALESCE(notes, '')",
}


def parse_filters(raw, is_public=False):
    """The client's JSON filter list, dropping anything malformed or not allowed."""
    try:
        filters = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(filters, list):
        return []

    parsed = []
    for entry in filters:
        if not isinstance(entry, dict):
            continue
        field = entry.get("field")
        exact = bool(entry.get("exact"))
        values = [str(v) for v in entry.get("values", [])]
        # An empty value says something only when it was quoted as exact ("material:``"
        # asks for the trips with no material); anywhere else it filters nothing.
        if not exact:
            values = [value for value in values if value]
        if field not in FILTER_FIELDS or not values:
            continue
        if is_public and field in PUBLIC_HIDDEN_FIELDS:
            continue
        parsed.append(
            {
                "field": field,
                "values": values,
                "exact": exact,
                "negate": bool(entry.get("negate")),
            }
        )
    return parsed


def _like(expression, param):
    return (
        f"remove_diacritics(LOWER({expression}))"
        f" LIKE remove_diacritics(LOWER(:{param}))"
    )


def _empty_predicate(field):
    """`field:``` — the field carries no value, in whichever column holds it."""
    if field == "material_type":
        columns = ["material_type", "iata", "manufacturer", "model"]
    elif field in _TEXT_FIELDS:
        columns = [_TEXT_FIELDS[field]]
    else:
        return f"{field} IS NULL"
    empty = " AND ".join(f"COALESCE({column}, '') = ''" for column in columns)
    return f"({empty})"


def _value_predicate(field, value, exact, param):
    """One predicate for one typed value, with the parameters it binds.

    A wildcard makes the match an anchored LIKE whether or not the value was quoted
    as exact, so `from:\\`Paris*\\`` still means "starts with Paris".
    """
    if exact and value == "":
        return _empty_predicate(field), {}

    comparison = comparison_condition(field, value, param)
    if comparison:
        return comparison

    if has_wildcard(value):
        exact = False
        pattern = like_pattern(value)
    elif exact:
        pattern = value
    else:
        pattern = like_pattern(value)
    params = {param: pattern}

    if field in _TEXT_FIELDS:
        expression = _TEXT_FIELDS[field]
        if exact:
            return f"LOWER({expression}) = LOWER(:{param})", params
        return _like(expression, param), params

    if field == "start_datetime":
        column = "COALESCE(to_char(start_datetime, 'YYYY-MM-DD'), '')"
        comparator = "=" if exact else "LIKE"
        return f"{column} {comparator} :{param}", params

    if field == "operator":
        if exact:
            match = f"LOWER(COALESCE(operator, '')) = LOWER(:{param})"
        else:
            match = _like("COALESCE(operator, '')", param)
        # Also match trips whose operator resolves to the same company under a
        # different spelling, so "operator:SBB" finds one logged as CFF. The names are
        # resolved to ids once here rather than per row, leaving an indexed integer
        # lookup in the correlated subquery.
        operator_ids = find_operator_ids(value, exact=exact)
        if operator_ids:
            ids_param = f"{param}_operator_ids"
            params[ids_param] = operator_ids
            match = (
                f"({match} OR EXISTS (SELECT 1 FROM trip_operators tvs"
                f" WHERE tvs.trip_id = FilteredTrips.uid"
                f" AND tvs.operator_id = ANY(:{ids_param})))"
            )
        return match, params

    if field == "material_type":
        # The vehicle is spelled out across several columns; any of them may carry it.
        columns = ["COALESCE(material_type, '')", "iata", "manufacturer", "model"]
        if exact:
            terms = [f"LOWER({column}) = LOWER(:{param})" for column in columns]
        else:
            terms = [_like(column, param) for column in columns]
        return "(" + " OR ".join(terms) + ")", params

    # Numeric columns that were not a comparison ("price:12,50" typed with a comma):
    # CAST to text first, since PG will not COALESCE a number with ''.
    column = f"COALESCE(CAST({field} AS text), '')"
    if exact:
        return f"LOWER({column}) = LOWER(:{param})", params
    return _like(column, param), params


def filter_conditions(filters):
    """(conditions, params) for the parsed filter list.

    One condition per filter, ORing the alternatives inside it. A negated filter is
    wrapped in NOT COALESCE(..., FALSE) so rows whose column is NULL (a missing
    operator, an undated trip) are kept by the exclusion rather than dropped by it.
    """
    conditions, params = [], {}
    for index, entry in enumerate(filters):
        alternatives = []
        for position, value in enumerate(entry["values"]):
            param = f"filter_{index}_{position}"
            sql, value_params = _value_predicate(
                entry["field"], value, entry["exact"], param
            )
            alternatives.append(sql)
            params.update(value_params)
        condition = (
            alternatives[0]
            if len(alternatives) == 1
            else "(" + " OR ".join(alternatives) + ")"
        )
        if entry["negate"]:
            condition = f"NOT COALESCE({condition}, FALSE)"
        conditions.append(condition)
    return conditions, params


def base_type_filter(filters, trip_types):
    """The trip type to push down into the base CTE, or None.

    The type filter itself is a diacritics-insensitive LIKE, which no index can serve,
    so the CTE would materialise every one of the user's trips and only then drop the
    other types. When the search names real types exactly and is not negated, the CTE
    can be constrained by trip_type as well, letting the (user_id, trip_type) index
    fetch just those rows. The LIKE stays on the outer query, so this only narrows the
    scan; a partial "type:fer" falls back to it alone.
    """
    types = set()
    for entry in filters:
        if entry["field"] != "type" or entry["negate"]:
            continue
        named = {value.strip().lower() for value in entry["values"]}
        if not named <= trip_types:
            return None
        types = named if not types else types & named
    return sorted(types) or None
