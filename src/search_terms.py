"""Turning what a user types in the trips search into SQL: LIKE patterns and,
for the numeric and date columns, comparisons and ranges."""

import re
from datetime import date, timedelta

# The LIKE metacharacters are literals in a typed term; `*` and `?` are the
# wildcards the user means by them.
_LIKE_ESCAPES = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def has_wildcard(term: str) -> bool:
    return "*" in term or "?" in term


def like_pattern(term: str, exact: bool = False) -> str:
    """`term` as a LIKE pattern, `*` matching any run of characters and `?` one.

    A wildcard term describes the whole value rather than a fragment of it, so it is
    anchored: "from:Paris*" is "starts with Paris", not "contains Paris anywhere".
    Anything else keeps the plain substring match, unless `exact` asks for the term
    on its own.
    """
    pattern = term.translate(_LIKE_ESCAPES).replace("*", "%").replace("?", "_")
    return pattern if exact or has_wildcard(term) else f"%{pattern}%"


# Comparison and range filters: "duration:>2h", "date:2024-01-01..2024-06-30".
#
# The column each one runs against, with the converter turning the typed value into
# the unit the column is stored in: trip_length is metres and trip_speed m/s, while
# both are typed (and displayed) in km.
_COMPARISON_RE = re.compile(r"^(>=|<=|>|<|=)\s*(.+)$")
_RANGE_SEPARATOR = ".."

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(h|min|m|s)?")
# A bare number is minutes, and so is the tail of "1h30".
_DURATION_UNITS = {"h": 3600, "min": 60, "m": 60, "s": 1, None: 60}
_DISTANCE_UNITS = {"": 1000, "km": 1000, "m": 1, "mi": 1609.344}
_SPEED_UNITS = {"": 1 / 3.6, "km/h": 1 / 3.6, "kmh": 1 / 3.6, "kph": 1 / 3.6,
                "m/s": 1, "ms": 1, "mph": 0.44704}
_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-z/]*)$")


def _parse_number(value, units=None):
    match = _NUMBER_RE.match(value.strip().lower().replace(",", "."))
    if not match:
        return None
    unit = match.group(2)
    if units is None:
        return float(match.group(1)) if not unit else None
    if unit not in units:
        return None
    return float(match.group(1)) * units[unit]


def _parse_duration(value):
    """Seconds from "2h", "1h30", "90min", "45s", "1:30" or a bare count of minutes."""
    typed = value.strip().lower().replace(",", ".")
    if ":" in typed:
        hours, _, minutes = typed.partition(":")
        if not (hours.isdigit() and minutes.isdigit()):
            return None
        return int(hours) * 3600 + int(minutes) * 60
    total, position = 0.0, 0
    for part in _DURATION_PART.finditer(typed):
        if part.start() != position:
            return None
        position = part.end()
        total += float(part.group(1)) * _DURATION_UNITS[part.group(2)]
    return total if position == len(typed) and total else None


def _parse_date_bounds(value):
    """The day, month or year as the (first, last) day it spans.

    A partial date is a span, which is what makes "date:>2024" mean "after 2024" and
    "date:<=2024-06" mean "up to the end of June" rather than either landing on the
    first of January.
    """
    typed = value.strip()
    try:
        if re.fullmatch(r"\d{4}", typed):
            year = int(typed)
            return date(year, 1, 1), date(year, 12, 31)
        if re.fullmatch(r"\d{4}-\d{2}", typed):
            first = date.fromisoformat(f"{typed}-01")
            return first, date(first.year + first.month // 12,
                              first.month % 12 + 1, 1) - timedelta(days=1)
        day = date.fromisoformat(typed)
    except ValueError:
        return None
    return day, day


def _bounds(parse, value):
    """(lower, upper) for a typed value; a plain number spans nothing but itself."""
    parsed = parse(value)
    if parsed is None:
        return None
    return parsed if isinstance(parsed, tuple) else (parsed, parsed)


_COMPARISON_FIELDS = {
    "start_datetime": ("start_datetime::date", _parse_date_bounds),
    "trip_duration_seconds": ("trip_duration_seconds", _parse_duration),
    "trip_length": ("trip_length", lambda v: _parse_number(v, _DISTANCE_UNITS)),
    "trip_speed": ("trip_speed", lambda v: _parse_number(v, _SPEED_UNITS)),
    "price": ("price", _parse_number),
}


def comparison_condition(column_name, term, param):
    """SQL for "duration:>2h" or "date:2024-01-01..2024-06-30", or None.

    None means the term is not a comparison and the caller should fall back to its
    text matching, so a plain "date:2024-01" keeps behaving as it always did.

    The strict operators are read against the span a partial value covers: "date:>2024"
    is after its last day, "date:<2024" before its first.
    """
    field = _COMPARISON_FIELDS.get(column_name)
    if not field:
        return None
    expression, parse = field

    low, high = f"{param}_low", f"{param}_high"
    if _RANGE_SEPARATOR in term:
        start, _, end = term.partition(_RANGE_SEPARATOR)
        start_bounds, end_bounds = _bounds(parse, start), _bounds(parse, end)
        if not (start_bounds and end_bounds):
            return None
        return (
            f"{expression} BETWEEN :{low} AND :{high}",
            {low: start_bounds[0], high: end_bounds[1]},
        )

    match = _COMPARISON_RE.match(term)
    if not match:
        # A bare value on a numeric column means the value itself ("duration:2h").
        # Dates keep their text matching, so "date:2024-01" still spans the month.
        bounds = None if column_name == "start_datetime" else _bounds(parse, term)
        if not bounds:
            return None
        return (
            f"{expression} BETWEEN :{low} AND :{high}",
            {low: bounds[0], high: bounds[1]},
        )
    operator, bounds = match.group(1), _bounds(parse, match.group(2))
    if not bounds:
        return None
    if operator == "=":
        return (
            f"{expression} BETWEEN :{low} AND :{high}",
            {low: bounds[0], high: bounds[1]},
        )
    # ">" and "<=" clear the whole span, ">=" and "<" stop at its edge.
    value = bounds[1] if operator in (">", "<=") else bounds[0]
    return f"{expression} {operator} :{param}_value", {f"{param}_value": value}
