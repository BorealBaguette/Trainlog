"""Per-mode totals for a set of trips, and the Discord block that exports them.

Feature request #68: people were writing their month up by hand for the Discord
channel, from figures the period page already draws as proportion bars. This
puts numbers on those bars and hands over the text to paste.

The trip ids come from the page, already screened for visibility, so nothing is
filtered again here — the summary and the trips listed above it would otherwise
disagree.
"""

from py.utils import get_flag_emoji
from src.pg import pg_session
from src.trip_types import TRIP_TYPES

# A trip with a mistyped year ("24" for 2024) claims a duration of centuries,
# which would swamp the total. Anything past this is data, not a journey.
MAX_DURATION = "30 days"


def _duration(seconds) -> str:
    """"2d 2h 51m", "6h 33m", "51m" — the shape the channel already writes."""
    minutes = round((seconds or 0) / 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _distance(metres) -> str:
    return f"{round((metres or 0) / 1000):,} km".replace(",", " ")


def trip_stats(trip_ids, labels=None) -> dict:
    """Distance and time per trip type, plus the countries those trips ran through.

    Returns {"modes": [{type, trips, distance, duration, ...}], "countries": [...],
    "trips": n, "distance": m, "duration": s} — modes in the registry's own
    order, so the list reads rail first however the trips were logged.

    `labels` is the caller's language dict, keyed by trip type as lang/ already
    keys them, so the summary reads in the user's own language.
    """
    labels = labels or {}
    if not trip_ids:
        return {
            "modes": [], "countries": [], "flags": "",
            "trips": 0, "distance": 0, "duration": 0,
        }

    with pg_session() as pg:
        modes = pg.execute(
            f"""
            SELECT trip_type,
                   COUNT(*) AS trips,
                   COALESCE(SUM(trip_length), 0) AS distance,
                   COALESCE(SUM(EXTRACT(EPOCH FROM (
                       COALESCE(utc_end_datetime, end_datetime)
                       - COALESCE(utc_start_datetime, start_datetime)
                   )) ) FILTER (
                       WHERE COALESCE(utc_end_datetime, end_datetime)
                             > COALESCE(utc_start_datetime, start_datetime)
                         AND COALESCE(utc_end_datetime, end_datetime)
                             - COALESCE(utc_start_datetime, start_datetime)
                             < interval '{MAX_DURATION}'
                   ), 0) AS duration
            FROM trips
            WHERE trip_id = ANY(:ids)
            GROUP BY trip_type
            """,
            {"ids": list(trip_ids)},
        ).fetchall()

        countries = pg.execute(
            """
            SELECT key AS code,
                   SUM(CASE
                       WHEN jsonb_typeof(value) = 'number' THEN value::numeric
                       ELSE (value->>'elec')::numeric
                            + COALESCE((value->>'nonelec')::numeric, 0)
                   END) AS km
            FROM trips, LATERAL jsonb_each(countries::jsonb)
            WHERE trip_id = ANY(:ids) AND countries IS NOT NULL AND key != 'UN'
            GROUP BY key
            ORDER BY km DESC
            """,
            {"ids": list(trip_ids)},
        ).fetchall()

    order = list(TRIP_TYPES)
    rows = sorted(
        (dict(row) for row in modes),
        key=lambda row: order.index(row["trip_type"])
        if row["trip_type"] in order
        else len(order),
    )
    for row in rows:
        row["distance_text"] = _distance(row["distance"])
        row["duration_text"] = _duration(row["duration"])
        row["icon"] = TRIP_TYPES.get(row["trip_type"], {}).get("icon", "")
        row["label"] = labels.get(row["trip_type"], row["trip_type"].capitalize())

    return {
        "modes": rows,
        "countries": [row["code"] for row in countries],
        "flags": "".join(get_flag_emoji(row["code"]) for row in countries),
        "trips": sum(row["trips"] for row in rows),
        "distance": sum(row["distance"] or 0 for row in rows),
        "duration": sum(row["duration"] or 0 for row in rows),
    }


def discord_summary(title, stats, url) -> str:
    """The stats as a Discord message, ready to paste into the channel."""
    lines = [f"# {title}", "**Distance & Times**:"]
    for row in stats["modes"]:
        lines.append(
            f"    **{row['label']}**: {row['distance_text']}, {row['duration_text']}"
        )
    lines.append(
        f"**Countries visited**: {len(stats['countries'])}({stats['flags']})"
    )
    lines.append(f"**Link**: [trainlog]({url})")
    return "\n".join(lines)
