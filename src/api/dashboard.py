import logging
import time
from datetime import date, datetime

from flask import Blueprint, jsonify, request

from py.currency import get_exchange_rate
from src.api.wrapped import get_wrapped_data
from src.pg import pg_session
from src.users import Friendship, User
from src.utils import get_user_id, login_required, mainConn, managed_cursor

logger = logging.getLogger(__name__)

dashboard_blueprint = Blueprint("user_dashboard", __name__)

# Simple in-process cache: {key: (timestamp, data)}
_cache = {}
_CACHE_TTL = 300  # 5 minutes


def _operator_logo(operator_name: str) -> str | None:
    """Return the latest logo_url for an operator by short_name, or None."""
    if not operator_name:
        return None
    with managed_cursor(mainConn) as cur:
        row = cur.execute(
            """
            SELECT l.logo_url
            FROM operators o
            JOIN operator_logos l ON l.operator_id = o.uid
            WHERE o.short_name = ?
            ORDER BY l.effective_date DESC
            LIMIT 1
            """,
            (operator_name,),
        ).fetchone()
    return row["logo_url"] if row else None


def _cache_get(key):
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key, data):
    _cache[key] = (time.time(), data)


@dashboard_blueprint.route("/u/<username>/dashboard_totals")
@login_required
def dashboard_totals(username):
    """All-time hero bar stats: trips, km, countries, duration."""
    user_id = get_user_id(username)
    with pg_session() as pg:
        row = pg.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE COALESCE(utc_start_datetime, start_datetime) < NOW()
                    AND NOT is_project
                ) AS total_trips,
                COALESCE(SUM(trip_length) FILTER (
                    WHERE COALESCE(utc_start_datetime, start_datetime) < NOW()
                    AND NOT is_project
                ), 0) / 1000 AS total_km,
                COALESCE(SUM(
                    CASE WHEN COALESCE(utc_start_datetime, start_datetime) < NOW()
                              AND NOT is_project
                    THEN COALESCE(
                        EXTRACT(EPOCH FROM (utc_end_datetime - utc_start_datetime)),
                        manual_trip_duration,
                        estimated_trip_duration,
                        0
                    )
                    ELSE 0 END
                ), 0) AS total_duration_sec
            FROM trips
            WHERE user_id = :uid
            """,
            {"uid": user_id},
        ).fetchone()

        countries_row = pg.execute(
            """
            SELECT COUNT(DISTINCT country_code) AS country_count
            FROM (
                SELECT key AS country_code
                FROM trips,
                LATERAL jsonb_each(countries::jsonb)
                WHERE user_id = :uid
                  AND COALESCE(utc_start_datetime, start_datetime) < NOW()
                  AND NOT is_project
                  AND countries IS NOT NULL
                  AND key != 'UN'
                  AND length(key) = 2
            ) cc
            """,
            {"uid": user_id},
        ).fetchone()

    return jsonify(
        {
            "total_trips": int(row.total_trips or 0),
            "total_km": int(row.total_km or 0),
            "total_duration_sec": int(row.total_duration_sec or 0),
            "country_count": int(countries_row.country_count or 0),
        }
    )


@dashboard_blueprint.route("/u/<username>/dashboard_trips")
@login_required
def dashboard_trips(username):
    """Today's trips, next 3 upcoming, last 3 recent."""
    user_id = get_user_id(username)

    def row_to_dict(r):
        return {
            "trip_id": r.trip_id,
            "origin": r.origin_station,
            "destination": r.destination_station,
            "start": r.start_datetime.isoformat() if r.start_datetime else None,
            "end": r.end_datetime.isoformat() if r.end_datetime else None,
            "operator": r.operator,
            "km": int((r.trip_length or 0) / 1000),
            "type": r.trip_type,
        }

    with pg_session() as pg:
        today_rows = pg.execute(
            """
            SELECT trip_id, origin_station, destination_station,
                   start_datetime, end_datetime, operator, trip_length, trip_type
            FROM trips
            WHERE user_id = :uid
              AND is_project = false
              AND DATE(COALESCE(utc_start_datetime, start_datetime)) = CURRENT_DATE
            ORDER BY COALESCE(utc_start_datetime, start_datetime)
            LIMIT 10
            """,
            {"uid": user_id},
        ).fetchall()

        upcoming_rows = pg.execute(
            """
            SELECT trip_id, origin_station, destination_station,
                   start_datetime, end_datetime, operator, trip_length, trip_type
            FROM trips
            WHERE user_id = :uid
              AND is_project = false
              AND COALESCE(utc_start_datetime, start_datetime) > NOW()
            ORDER BY COALESCE(utc_start_datetime, start_datetime) ASC
            LIMIT 3
            """,
            {"uid": user_id},
        ).fetchall()

        recent_rows = pg.execute(
            """
            SELECT trip_id, origin_station, destination_station,
                   start_datetime, end_datetime, operator, trip_length, trip_type
            FROM trips
            WHERE user_id = :uid
              AND is_project = false
              AND COALESCE(utc_start_datetime, start_datetime) < NOW()
              AND DATE(COALESCE(utc_start_datetime, start_datetime)) < CURRENT_DATE
            ORDER BY COALESCE(utc_start_datetime, start_datetime) DESC
            LIMIT 5
            """,
            {"uid": user_id},
        ).fetchall()

    return jsonify(
        {
            "today": [row_to_dict(r) for r in today_rows],
            "upcoming": [row_to_dict(r) for r in upcoming_rows],
            "recent": [row_to_dict(r) for r in recent_rows],
        }
    )


@dashboard_blueprint.route("/u/<username>/dashboard_friends")
@login_required
def dashboard_friends(username):
    """Friends' last 5 trips and friends currently traveling."""
    user = User.query.filter_by(username=username).first_or_404()

    # Get accepted friends from SQLite (authDb)
    friends = (
        User.query.join(
            Friendship,
            ((Friendship.user_id == user.uid) & (Friendship.friend_id == User.uid))
            | ((Friendship.friend_id == user.uid) & (Friendship.user_id == User.uid)),
        )
        .filter(Friendship.accepted != None)  # noqa: E711
        .all()
    )

    if not friends:
        return jsonify(
            {"recent": [], "upcoming": [], "current": [], "has_friends": False}
        )

    friend_ids = [f.uid for f in friends]
    friend_usernames = {f.uid: f.username for f in friends}

    def trip_dict(r):
        return {
            "trip_id": r.trip_id,
            "username": friend_usernames.get(r.user_id, "?"),
            "origin": r.origin_station,
            "destination": r.destination_station,
            "start": r.start_datetime.isoformat() if r.start_datetime else None,
            "operator": r.operator,
            "km": int((r.trip_length or 0) / 1000),
            "type": r.trip_type,
        }

    with pg_session() as pg:
        recent_rows = pg.execute(
            """
            SELECT trip_id, user_id, origin_station, destination_station,
                   start_datetime, operator, trip_length, trip_type
            FROM trips
            WHERE user_id = ANY(:ids)
              AND is_project = false
              AND visibility IN ('public', 'friends')
              AND COALESCE(utc_start_datetime, start_datetime) < NOW()
            ORDER BY COALESCE(utc_start_datetime, start_datetime) DESC
            LIMIT 5
            """,
            {"ids": friend_ids},
        ).fetchall()

        upcoming_rows = pg.execute(
            """
            SELECT trip_id, user_id, origin_station, destination_station,
                   start_datetime, operator, trip_length, trip_type
            FROM trips
            WHERE user_id = ANY(:ids)
              AND is_project = false
              AND visibility IN ('public', 'friends')
              AND COALESCE(utc_start_datetime, start_datetime) > NOW()
            ORDER BY COALESCE(utc_start_datetime, start_datetime) ASC
            LIMIT 5
            """,
            {"ids": friend_ids},
        ).fetchall()

        current_rows = pg.execute(
            """
            SELECT trip_id, user_id, origin_station, destination_station,
                   start_datetime, operator, trip_length, trip_type
            FROM trips
            WHERE user_id = ANY(:ids)
              AND is_project = false
              AND visibility IN ('public', 'friends')
              AND COALESCE(utc_start_datetime, start_datetime)
                  + COALESCE(departure_delay, 0) * interval '1 second' <= NOW()
              AND COALESCE(utc_end_datetime, end_datetime)
                  + COALESCE(arrival_delay, 0) * interval '1 second' >= NOW()
            """,
            {"ids": friend_ids},
        ).fetchall()

    return jsonify(
        {
            "recent": [trip_dict(r) for r in recent_rows],
            "upcoming": [trip_dict(r) for r in upcoming_rows],
            "current": [trip_dict(r) for r in current_rows],
            "has_friends": True,
        }
    )


@dashboard_blueprint.route("/u/<username>/dashboard_leaderboard")
@login_required
def dashboard_leaderboard(username):
    """Per-type rank among all users by km (regardless of leaderboard opt-in)."""
    user_id = get_user_id(username)
    try:
        year = int(request.args.get("year", 0)) or None
    except (ValueError, TypeError):
        year = None

    year_filter = (
        "AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr"
        if year
        else ""
    )
    p = {"uid": user_id, "yr": year} if year else {"uid": user_id}

    with pg_session() as pg:
        rows = pg.execute(
            f"""
            WITH all_totals AS (
                SELECT user_id, trip_type,
                       COUNT(*) AS trips,
                       COALESCE(SUM(trip_length), 0) AS total_length
                FROM trips
                WHERE COALESCE(utc_start_datetime, start_datetime) < NOW()
                  AND NOT is_project
                  {year_filter}
                GROUP BY user_id, trip_type
                UNION ALL
                SELECT user_id, 'all' AS trip_type,
                       COUNT(*) AS trips,
                       COALESCE(SUM(trip_length), 0) AS total_length
                FROM trips
                WHERE COALESCE(utc_start_datetime, start_datetime) < NOW()
                  AND NOT is_project
                  {year_filter}
                GROUP BY user_id
            ),
            ranked AS (
                SELECT user_id, trip_type, trips, total_length,
                       RANK() OVER (PARTITION BY trip_type ORDER BY total_length DESC) AS km_rank,
                       RANK() OVER (PARTITION BY trip_type ORDER BY trips DESC) AS trips_rank,
                       COUNT(*) OVER (PARTITION BY trip_type) AS total_users
                FROM all_totals
            )
            SELECT trip_type, trips, total_length, km_rank, trips_rank, total_users
            FROM ranked
            WHERE user_id = :uid
            ORDER BY trip_type
            """,
            p,
        ).fetchall()

    by_type = {
        r.trip_type: {
            "km_rank": int(r.km_rank),
            "trips_rank": int(r.trips_rank),
            "total": int(r.total_users),
            "trips": int(r.trips),
            "km": int((r.total_length or 0) / 1000),
        }
        for r in rows
    }
    return jsonify(by_type)


@dashboard_blueprint.route("/u/<username>/dashboard_years")
@login_required
def dashboard_years(username):
    """List of years the user has trip data for."""
    user_id = get_user_id(username)
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT DISTINCT EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime))::int AS year
            FROM trips
            WHERE user_id = :uid AND NOT is_project
              AND COALESCE(utc_start_datetime, start_datetime) < NOW()
            ORDER BY year DESC
            """,
            {"uid": user_id},
        ).fetchall()
    return jsonify({"years": [r.year for r in rows]})


@dashboard_blueprint.route("/u/<username>/dashboard_year")
@login_required
def dashboard_year(username):
    """Highlights & records data for a given year (or all-time), cached 5 min."""
    current_year = datetime.now().year
    try:
        selected_year = int(request.args.get("year", current_year))
    except (ValueError, TypeError):
        selected_year = current_year

    user_obj = User.query.filter_by(username=username).first()
    user_currency = user_obj.user_currency if user_obj else "EUR"

    cache_key = f"dashboard_year:{username}:{selected_year}:{user_currency}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return jsonify(cached)

    is_current_year = selected_year == current_year
    # For current year: cut off at today (YTD). For past years: include full year.
    if is_current_year:
        cutoff = datetime.now()
        prev_cutoff = datetime(
            current_year - 1,
            datetime.now().month,
            datetime.now().day,
            datetime.now().hour,
            datetime.now().minute,
        )
    else:
        cutoff = datetime(selected_year, 12, 31, 23, 59, 59)
        prev_cutoff = datetime(selected_year - 1, 12, 31, 23, 59, 59)

    data = get_wrapped_data(username, str(selected_year), "combined")
    user_id = get_user_id(username)

    def serialize(val):
        if isinstance(val, (datetime, date)):
            return val.isoformat()
        return val

    def serialize_dict(d):
        if d is None:
            return None
        return {k: serialize(v) for k, v in d.items()}

    def pct_change(new_val, old_val):
        if not old_val:
            return None
        return round((new_val - old_val) / old_val * 100)

    _SPEED_EXPR = """
        COALESCE(
            EXTRACT(EPOCH FROM (utc_end_datetime - utc_start_datetime)),
            manual_trip_duration,
            estimated_trip_duration
        )
    """

    p = {
        "uid": user_id,
        "yr": selected_year,
        "cutoff": cutoff,
        "prev_cutoff": prev_cutoff,
    }

    with pg_session() as pg:
        # ── This year card ────────────────────────────────────────────────────
        ytd_row = pg.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
                    AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
                    AND NOT is_project
                ) AS this_ytd_trips,
                COALESCE(SUM(trip_length) FILTER (
                    WHERE EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
                    AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
                    AND NOT is_project
                ), 0) / 1000 AS this_ytd_km,
                COUNT(*) FILTER (
                    WHERE EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr - 1
                    AND COALESCE(utc_start_datetime, start_datetime) <= :prev_cutoff
                    AND NOT is_project
                ) AS prev_ytd_trips,
                COALESCE(SUM(trip_length) FILTER (
                    WHERE EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr - 1
                    AND COALESCE(utc_start_datetime, start_datetime) <= :prev_cutoff
                    AND NOT is_project
                ), 0) / 1000 AS prev_ytd_km
            FROM trips WHERE user_id = :uid
            """,
            p,
        ).fetchone()

        # ── Top operators ─────────────────────────────────────────────────────
        top_ops_alltime = pg.execute(
            """
            SELECT operator, COUNT(*) AS trips
            FROM trips
            WHERE user_id = :uid AND operator IS NOT NULL AND operator != ''
              AND COALESCE(utc_start_datetime, start_datetime) < NOW()
              AND NOT is_project
            GROUP BY operator ORDER BY trips DESC LIMIT 3
            """,
            p,
        ).fetchall()

        top_ops_ytd = pg.execute(
            """
            SELECT operator, COUNT(*) AS trips
            FROM trips
            WHERE user_id = :uid AND operator IS NOT NULL AND operator != ''
              AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
              AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
              AND NOT is_project
            GROUP BY operator ORDER BY trips DESC LIMIT 3
            """,
            p,
        ).fetchall()

        # ── Top routes ────────────────────────────────────────────────────────
        top_routes_alltime = pg.execute(
            """
            SELECT origin_station || ' → ' || destination_station AS name, COUNT(*) AS count
            FROM trips
            WHERE user_id = :uid
              AND origin_station IS NOT NULL AND origin_station != ''
              AND destination_station IS NOT NULL AND destination_station != ''
              AND COALESCE(utc_start_datetime, start_datetime) < NOW()
              AND NOT is_project
            GROUP BY origin_station, destination_station ORDER BY count DESC LIMIT 3
            """,
            p,
        ).fetchall()

        top_routes_ytd = pg.execute(
            """
            SELECT origin_station || ' → ' || destination_station AS name, COUNT(*) AS count
            FROM trips
            WHERE user_id = :uid
              AND origin_station IS NOT NULL AND origin_station != ''
              AND destination_station IS NOT NULL AND destination_station != ''
              AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
              AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
              AND NOT is_project
            GROUP BY origin_station, destination_station ORDER BY count DESC LIMIT 3
            """,
            p,
        ).fetchall()

        # ── Top countries ─────────────────────────────────────────────────────
        _countries_base = """
            SELECT key AS country_code,
                   COALESCE(SUM(
                       CASE WHEN jsonb_typeof(value) = 'number' THEN value::numeric
                            ELSE (value->>'elec')::numeric + COALESCE((value->>'nonelec')::numeric, 0)
                       END
                   ), 0) / 1000 AS km
            FROM trips, LATERAL jsonb_each(countries::jsonb)
            WHERE user_id = :uid AND NOT is_project
              AND countries IS NOT NULL
              AND key != 'UN' AND length(key) = 2
        """
        top_countries_alltime = pg.execute(
            _countries_base
            + " AND COALESCE(utc_start_datetime, start_datetime) < NOW()"
            " GROUP BY key ORDER BY km DESC LIMIT 5",
            p,
        ).fetchall()

        top_countries_ytd = pg.execute(
            _countries_base
            + " AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr"
            " AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff"
            " GROUP BY key ORDER BY km DESC LIMIT 5",
            p,
        ).fetchall()

        # ── Longest trip ──────────────────────────────────────────────────────
        longest_alltime = pg.execute(
            """
            SELECT origin_station, destination_station, trip_length
            FROM trips
            WHERE user_id = :uid AND NOT is_project AND trip_length > 0
              AND trip_type NOT IN ('accommodation', 'poi', 'restaurant')
              AND COALESCE(utc_start_datetime, start_datetime) < NOW()
            ORDER BY trip_length DESC LIMIT 1
            """,
            p,
        ).fetchone()

        longest_ytd = pg.execute(
            """
            SELECT origin_station, destination_station, trip_length
            FROM trips
            WHERE user_id = :uid AND NOT is_project AND trip_length > 0
              AND trip_type NOT IN ('accommodation', 'poi', 'restaurant')
              AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
              AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
            ORDER BY trip_length DESC LIMIT 1
            """,
            p,
        ).fetchone()

        # ── Fastest trip ──────────────────────────────────────────────────────
        _fastest_base = f"""
            SELECT origin_station, destination_station, trip_length,
                   ({_SPEED_EXPR}) AS duration_sec
            FROM trips
            WHERE user_id = :uid AND NOT is_project AND trip_length > 0
              AND ({_SPEED_EXPR}) > 0
        """
        fastest_alltime = pg.execute(
            _fastest_base + f" AND COALESCE(utc_start_datetime, start_datetime) < NOW()"
            f" ORDER BY trip_length / ({_SPEED_EXPR}) DESC LIMIT 1",
            p,
        ).fetchone()

        fastest_ytd = pg.execute(
            _fastest_base
            + f" AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr"
            f" AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff"
            f" ORDER BY trip_length / ({_SPEED_EXPR}) DESC LIMIT 1",
            p,
        ).fetchone()

        # ── Streak ────────────────────────────────────────────────────────────
        _streak_cte = """
            WITH travel_days AS (
                SELECT DISTINCT DATE(COALESCE(utc_start_datetime, start_datetime)) AS travel_date
                FROM trips
                WHERE user_id = :uid AND NOT is_project
                  {date_filter}
            ),
            with_gaps AS (
                SELECT travel_date,
                       travel_date - (ROW_NUMBER() OVER (ORDER BY travel_date))::int AS grp
                FROM travel_days
            ),
            streaks AS (
                SELECT MIN(travel_date) AS streak_start,
                       MAX(travel_date) AS streak_end,
                       COUNT(*) AS streak_length
                FROM with_gaps GROUP BY grp
            )
            SELECT streak_start, streak_end, streak_length
            FROM streaks ORDER BY streak_length DESC LIMIT 1
        """
        streak_alltime = pg.execute(
            _streak_cte.format(
                date_filter="AND COALESCE(utc_start_datetime, start_datetime) < NOW()"
            ),
            p,
        ).fetchone()

        streak_ytd = pg.execute(
            _streak_cte.format(
                date_filter="AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr"
                " AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff"
            ),
            p,
        ).fetchone()

        # ── Percentile (selected year, across all users) ──────────────────────
        percentile_row = pg.execute(
            """
            WITH user_totals AS (
                SELECT user_id,
                       COALESCE(SUM(trip_length), 0) AS total_km,
                       COUNT(*) AS total_trips
                FROM trips
                WHERE EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
                  AND NOT is_project
                  AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
                GROUP BY user_id
            ),
            ranked AS (
                SELECT user_id,
                       PERCENT_RANK() OVER (ORDER BY total_km)   * 100 AS km_percentile,
                       PERCENT_RANK() OVER (ORDER BY total_trips) * 100 AS trips_percentile,
                       COUNT(*) OVER () AS total_users
                FROM user_totals
            )
            SELECT km_percentile, trips_percentile, total_users
            FROM ranked WHERE user_id = :uid
            """,
            p,
        ).fetchone()

        # ── Per-mode stats ────────────────────────────────────────────────────
        _dur_expr = (
            "COALESCE(EXTRACT(EPOCH FROM (utc_end_datetime - utc_start_datetime)),"
            " manual_trip_duration, estimated_trip_duration, 0)"
        )
        mode_alltime_rows = pg.execute(
            f"""
            SELECT trip_type,
                   COUNT(*) AS trips,
                   COALESCE(SUM(trip_length), 0) / 1000 AS km,
                   COALESCE(SUM({_dur_expr}), 0) AS duration_sec
            FROM trips
            WHERE user_id = :uid AND NOT is_project
              AND COALESCE(utc_start_datetime, start_datetime) < NOW()
            GROUP BY trip_type ORDER BY km DESC
            """,
            p,
        ).fetchall()

        mode_ytd_rows = pg.execute(
            f"""
            SELECT trip_type,
                   COUNT(*) AS trips,
                   COALESCE(SUM(trip_length), 0) / 1000 AS km,
                   COALESCE(SUM({_dur_expr}), 0) AS duration_sec
            FROM trips
            WHERE user_id = :uid AND NOT is_project
              AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
              AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
            GROUP BY trip_type ORDER BY km DESC
            """,
            p,
        ).fetchall()

        # ── CO2 ───────────────────────────────────────────────────────────────
        co2_alltime_rows = pg.execute(
            """
            SELECT trip_type, COALESCE(SUM(carbon), 0) AS kg
            FROM trips
            WHERE user_id = :uid AND NOT is_project
              AND carbon IS NOT NULL
              AND COALESCE(utc_start_datetime, start_datetime) < NOW()
            GROUP BY trip_type
            """,
            p,
        ).fetchall()

        co2_ytd_rows = pg.execute(
            """
            SELECT trip_type, COALESCE(SUM(carbon), 0) AS kg
            FROM trips
            WHERE user_id = :uid AND NOT is_project
              AND carbon IS NOT NULL
              AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
              AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
            GROUP BY trip_type
            """,
            p,
        ).fetchall()

        # ── Money – individual prices per currency × type ─────────────────────
        money_ind_alltime_rows = pg.execute(
            """
            SELECT trip_type, currency, COALESCE(SUM(price), 0) AS total
            FROM trips
            WHERE user_id = :uid AND NOT is_project
              AND price IS NOT NULL AND price > 0
              AND currency IS NOT NULL
              AND COALESCE(utc_start_datetime, start_datetime) < NOW()
            GROUP BY trip_type, currency
            """,
            p,
        ).fetchall()

        money_ind_ytd_rows = pg.execute(
            """
            SELECT trip_type, currency, COALESCE(SUM(price), 0) AS total
            FROM trips
            WHERE user_id = :uid AND NOT is_project
              AND price IS NOT NULL AND price > 0
              AND currency IS NOT NULL
              AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
              AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
            GROUP BY trip_type, currency
            """,
            p,
        ).fetchall()

        # Ticket usage by trip_type — all time (denominator for proportional split)
        ticket_by_type_total_rows = pg.execute(
            """
            SELECT ticket_id, trip_type, COUNT(*) AS cnt
            FROM trips
            WHERE user_id = :uid AND NOT is_project AND ticket_id IS NOT NULL
            GROUP BY ticket_id, trip_type
            """,
            p,
        ).fetchall()

        # Ticket usage by trip_type — selected year
        ticket_by_type_ytd_rows = pg.execute(
            """
            SELECT ticket_id, trip_type, COUNT(*) AS cnt
            FROM trips
            WHERE user_id = :uid AND NOT is_project AND ticket_id IS NOT NULL
              AND EXTRACT(YEAR FROM COALESCE(utc_start_datetime, start_datetime)) = :yr
              AND COALESCE(utc_start_datetime, start_datetime) <= :cutoff
            GROUP BY ticket_id, trip_type
            """,
            p,
        ).fetchall()

    # ── Resolve user currency (fetched before cache check above) ─────────────
    today = date.today()

    def _to_user_currency(amount, currency):
        if not amount or not currency:
            return 0.0
        try:
            converted = get_exchange_rate(float(amount), currency, user_currency, today)
            return float(converted) if converted is not None else 0.0
        except Exception:
            return 0.0

    # ── Ticket shares from SQLite, split by trip_type ─────────────────────────
    # Build lookup: {ticket_id: {trip_type: cnt}} for total and ytd
    def _group_ticket_rows(rows):
        d = {}
        for r in rows:
            d.setdefault(r.ticket_id, {})[r.trip_type] = r.cnt
        return d

    ticket_total_by_type = _group_ticket_rows(ticket_by_type_total_rows)
    ticket_ytd_by_type = _group_ticket_rows(ticket_by_type_ytd_rows)

    # {trip_type: amount} for alltime and ytd ticket shares
    ticket_shares_alltime_by_type = {}
    ticket_shares_ytd_by_type = {}

    all_ticket_ids = list(ticket_total_by_type.keys())
    if all_ticket_ids:
        placeholders = ",".join("?" * len(all_ticket_ids))
        with managed_cursor(mainConn) as cur:
            cur.execute(
                f"SELECT uid, price, currency FROM tickets WHERE uid IN ({placeholders})",
                all_ticket_ids,
            )
            for row in cur.fetchall():
                tid, tprice, tcurrency = row["uid"], row["price"], row["currency"]
                type_counts_total = ticket_total_by_type.get(tid, {})
                type_counts_ytd = ticket_ytd_by_type.get(tid, {})
                total_trips = sum(type_counts_total.values()) or 1
                converted_price = _to_user_currency(tprice, tcurrency)
                per_trip = converted_price / total_trips
                for ttype, cnt in type_counts_total.items():
                    ticket_shares_alltime_by_type[ttype] = (
                        ticket_shares_alltime_by_type.get(ttype, 0.0) + per_trip * cnt
                    )
                for ttype, cnt in type_counts_ytd.items():
                    ticket_shares_ytd_by_type[ttype] = (
                        ticket_shares_ytd_by_type.get(ttype, 0.0) + per_trip * cnt
                    )

    # ── Build money totals ────────────────────────────────────────────────────
    def _build_money(rows, ticket_by_type):
        by_type = {}
        for r in rows:
            converted = _to_user_currency(r.total, r.currency)
            by_type[r.trip_type] = by_type.get(r.trip_type, 0.0) + converted
        # Merge ticket shares into by_type
        for ttype, share in ticket_by_type.items():
            by_type[ttype] = by_type.get(ttype, 0.0) + share
        grand_total = sum(by_type.values())
        return {
            "total": round(grand_total, 2),
            "by_type": {k: round(v, 2) for k, v in by_type.items()},
            "currency": user_currency,
        }

    # ── Build CO2 totals ──────────────────────────────────────────────────────
    def _build_co2(rows):
        by_type = {}
        total = 0.0
        for r in rows:
            by_type[r.trip_type] = round(float(r.kg), 1)
            total += float(r.kg)
        return {"total": round(total, 1), "by_type": by_type}

    trips_change = pct_change(
        int(ytd_row.this_ytd_trips or 0), int(ytd_row.prev_ytd_trips or 0)
    )
    km_change = pct_change(int(ytd_row.this_ytd_km or 0), int(ytd_row.prev_ytd_km or 0))

    def _longest(row):
        if not row:
            return None
        return {
            "origin": row.origin_station,
            "destination": row.destination_station,
            "km": int(row.trip_length / 1000),
        }

    def _fastest(row):
        if not row:
            return None
        speed = round((row.trip_length / 1000.0) / (row.duration_sec / 3600.0))
        return {
            "origin": row.origin_station,
            "destination": row.destination_station,
            "speed": speed,
        }

    def _streak(row):
        if not row or row.streak_length <= 1:
            return None
        return {
            "days": int(row.streak_length),
            "start": row.streak_start.isoformat(),
            "end": row.streak_end.isoformat(),
        }

    def _countries(rows):
        return [{"code": r.country_code, "km": int(r.km)} for r in rows]

    result = {
        "year": selected_year,
        "total_trips": data.get("total_trips"),
        "total_km": data.get("total_km"),
        "total_duration": data.get("total_duration"),
        "trips_change": trips_change,
        "km_change": km_change,
        "busiest_month": serialize_dict(data.get("busiest_month")),
        "distance_comparisons": data.get("distance_comparisons", [])[:2],
        "duration_hours": data.get("duration_hours"),
        # Highlights
        "top_operators_alltime": [
            {"name": r.operator, "trips": r.trips, "logo": _operator_logo(r.operator)}
            for r in top_ops_alltime
        ],
        "top_operators_ytd": [
            {"name": r.operator, "trips": r.trips, "logo": _operator_logo(r.operator)}
            for r in top_ops_ytd
        ],
        "top_routes_alltime": [
            {"name": r.name, "count": r.count} for r in top_routes_alltime
        ],
        "top_routes_ytd": [{"name": r.name, "count": r.count} for r in top_routes_ytd],
        "top_countries_alltime": _countries(top_countries_alltime),
        "top_countries_ytd": _countries(top_countries_ytd),
        # Records
        "longest_trip_alltime": _longest(longest_alltime),
        "longest_trip_ytd": _longest(longest_ytd),
        "fastest_trip_alltime": _fastest(fastest_alltime),
        "fastest_trip_ytd": _fastest(fastest_ytd),
        "streak_alltime": _streak(streak_alltime),
        "streak_ytd": _streak(streak_ytd),
        "percentile": {
            "km": round(float(percentile_row.km_percentile), 2),
            "trips": round(float(percentile_row.trips_percentile), 2),
            "total_users": int(percentile_row.total_users),
        }
        if percentile_row and percentile_row.total_users > 1
        else None,
        # Money
        "money_alltime": _build_money(
            money_ind_alltime_rows, ticket_shares_alltime_by_type
        ),
        "money_ytd": _build_money(money_ind_ytd_rows, ticket_shares_ytd_by_type),
        # CO2
        "co2_alltime": _build_co2(co2_alltime_rows),
        "co2_ytd": _build_co2(co2_ytd_rows),
        # Per-mode breakdown
        "mode_alltime": [
            {
                "type": r.trip_type,
                "trips": int(r.trips),
                "km": round(float(r.km), 1),
                "sec": int(r.duration_sec),
            }
            for r in mode_alltime_rows
        ],
        "mode_ytd": [
            {
                "type": r.trip_type,
                "trips": int(r.trips),
                "km": round(float(r.km), 1),
                "sec": int(r.duration_sec),
            }
            for r in mode_ytd_rows
        ],
    }

    _cache_set(cache_key, result)
    return jsonify(result)
