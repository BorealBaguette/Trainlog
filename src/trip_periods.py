"""Date-range pseudo-tags: every trip a user made in a year, month or week.

These behave like a tag that nobody has to create and that nobody has to keep
up to date — the id list is resolved on every request, so a trip logged later
shows up on the page it belongs to:

    /public/simfr24/year/2026
    /public/simfr24/month/2026-10
    /public/simfr24/week/2026-W40

The window is matched against ``start_datetime``, which is the trip's local
departure time, so a 23:30 departure belongs to the day it felt like rather
than to whatever the UTC clock said.

Labels stay in ISO form ("2026-10") rather than "October 2026": month names
would need 12 new strings in each of the 25 locales, and ISO reads the same in
all of them.
"""

import re
from datetime import date, datetime, timedelta

from src.pg import pg_session

KINDS = ("year", "month", "week")

_YEAR_RE = re.compile(r"^(\d{4})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_WEEK_RE = re.compile(r"^(\d{4})-[Ww](\d{2})$")

# Trips are logged well before and after today, but not in the year 3000.
_MIN_YEAR, _MAX_YEAR = 1800, 2999


def parse_period(kind: str, value: str):
    """Half-open [start, end) local datetime window for a period. Raises ValueError."""
    if kind == "year":
        match = _YEAR_RE.match(value or "")
        if not match:
            raise ValueError(f"bad year: {value!r}")
        year = _check_year(int(match.group(1)))
        return datetime(year, 1, 1), datetime(year + 1, 1, 1)

    if kind == "month":
        match = _MONTH_RE.match(value or "")
        if not match:
            raise ValueError(f"bad month: {value!r}")
        year = _check_year(int(match.group(1)))
        month = int(match.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"bad month: {value!r}")
        start = datetime(year, month, 1)
        end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        return start, end

    if kind == "week":
        match = _WEEK_RE.match(value or "")
        if not match:
            raise ValueError(f"bad week: {value!r}")
        year = _check_year(int(match.group(1)))
        week = int(match.group(2))
        if not 1 <= week <= 53:
            raise ValueError(f"bad week: {value!r}")
        try:
            # ISO weeks: Monday is day 1, and week 53 does not exist every year.
            monday = date.fromisocalendar(year, week, 1)
        except ValueError as exc:
            raise ValueError(f"bad week: {value!r}") from exc
        start = datetime(monday.year, monday.month, monday.day)
        return start, start + timedelta(days=7)

    raise ValueError(f"bad period kind: {kind!r}")


def _check_year(year: int) -> int:
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        raise ValueError(f"year out of range: {year}")
    return year


def period_label(kind: str, value: str) -> str:
    """Canonical label for the page title — "2026", "2026-10", "2026-W40"."""
    if kind == "week":
        year, week = _WEEK_RE.match(value).groups()
        return f"{year}-W{week}"
    return value


def period_trip_ids(user_id: int, start: datetime, end: datetime) -> list:
    """That user's trip ids departing inside the window, oldest first.

    Visibility is not filtered here: the page that renders these screens every
    trip anyway, and doing it twice would only make the two disagree.
    """
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT trip_id
            FROM trips
            WHERE user_id = :user_id
              AND start_datetime >= :start
              AND start_datetime < :end
            ORDER BY start_datetime
            """,
            {"user_id": user_id, "start": start, "end": end},
        ).fetchall()
    return [row["trip_id"] for row in rows]
