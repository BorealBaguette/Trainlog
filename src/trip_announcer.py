"""Post a user's trips to Discord as they depart.

Several users type their trips into the Discord channel by hand as they take
them, having already logged the same trip here. This posts it for them.

Scheduling, without a scheduler
-------------------------------
Trainlog is a request/response Flask app with no cron, no celery and no
gateway process, so the announcer is a daemon thread started at import time —
the same shape as the email listener. Two things make that safe under gunicorn,
where the thread runs in *every* worker:

* Before a trip is posted, a row is claimed with ``INSERT ... ON CONFLICT DO
  NOTHING RETURNING``. Exactly one worker gets the row back, and only that one
  calls Discord. Duplicate posts are therefore impossible however many workers
  run, and a restart mid-tick cannot repost.
* Only departures inside a short window are eligible, so an outage means a few
  missed posts rather than a flood of stale ones when the app comes back.

The window is matched on ``utc_start_datetime``, the only comparable clock
across users in different time zones; what gets *printed* is the trip's local
time, which is what the traveller would have typed themselves.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from py.utils import get_flag_emoji
from src.consts import Env
from src.discord_bot import post_webhook_message
from src.pg import pg_session
from src.trip_card import render_trip_card
from src.users import User

logger = logging.getLogger(__name__)

SITE_URL = "https://trainlog.me"

POLL_SECONDS = 60

# How late a departure may be and still be worth announcing. Long enough that a
# slow tick, a restart or a trip logged from the platform still makes it out;
# short enough that nothing stale is posted after an outage.
ANNOUNCE_WINDOW = timedelta(minutes=15)

# Clocks drift, and a trip logged "for the 08:00" a few seconds early should not
# have to wait a whole tick.
FUTURE_GRACE = timedelta(minutes=1)

# Not from src.trip_types: that module is the source of truth for how a type is
# drawn in the app (Font Awesome classes, map colours), and Discord has no use
# for either.
TYPE_EMOJI = {
    "train": "🚆", "tram": "🚊", "metro": "🚇", "funicular": "🚞", "rail": "🚂",
    "air": "✈️", "bus": "🚌", "ferry": "⛴️", "helicopter": "🚁",
    "aerialway": "🚡", "walk": "🚶", "cycle": "🚲", "ski": "⛷️",
    "scooter": "🛴", "car": "🚗",
}


def _opted_in():
    """{user_id: (username, discord_id)} for users who asked for this.

    Needs an app context — the opt-in lives in the SQLite auth db, not in PG.
    A linked Discord account is required: opting in is done from there.
    """
    users = User.query.filter(
        User.discord_autopost.is_(True), User.discord_id.isnot(None)
    ).all()
    return {user.uid: (user.username, user.discord_id) for user in users}


def _due_trips(user_ids, now):
    """Public, non-project trips of those users departing inside the window.

    Departure means the *actual* one: a train logged as +20 is announced when
    it really left, not when it was timetabled to.

    The LEFT JOIN skips trips already announced. It is only an optimisation —
    the claim in _claim() is what actually guarantees one post per trip.
    """
    with pg_session() as pg:
        return pg.execute(
            """
            SELECT t.trip_id, t.user_id, t.operator, t.line_name, t.trip_type,
                   t.origin_station, t.destination_station, t.countries,
                   t.start_datetime, t.end_datetime,
                   t.departure_delay, t.arrival_delay
            FROM trips t
            LEFT JOIN trip_announcements a ON a.trip_id = t.trip_id
            WHERE t.user_id = ANY(:user_ids)
              AND t.visibility = 'public'
              AND NOT COALESCE(t.is_project, FALSE)
              AND t.utc_start_datetime
                  + make_interval(secs => COALESCE(t.departure_delay, 0)) >= :window_start
              AND t.utc_start_datetime
                  + make_interval(secs => COALESCE(t.departure_delay, 0)) <= :window_end
              AND a.trip_id IS NULL
            ORDER BY t.utc_start_datetime
                     + make_interval(secs => COALESCE(t.departure_delay, 0))
            """,
            {
                "user_ids": list(user_ids),
                "window_start": now - ANNOUNCE_WINDOW,
                "window_end": now + FUTURE_GRACE,
            },
        ).fetchall()


def _claim(trip_id) -> bool:
    """Take responsibility for announcing this trip. True in at most one caller."""
    with pg_session() as pg:
        row = pg.execute(
            """
            INSERT INTO trip_announcements (trip_id) VALUES (:trip_id)
            ON CONFLICT (trip_id) DO NOTHING
            RETURNING trip_id
            """,
            {"trip_id": trip_id},
        ).fetchone()
    return row is not None


def _release(trip_id):
    """Give up a claim so a later tick can retry (see announce_due_trips)."""
    with pg_session() as pg:
        pg.execute(
            "DELETE FROM trip_announcements WHERE trip_id = :trip_id AND message_id IS NULL",
            {"trip_id": trip_id},
        )


def _record_message(trip_id, message_id):
    with pg_session() as pg:
        pg.execute(
            "UPDATE trip_announcements SET message_id = :message_id WHERE trip_id = :trip_id",
            {"trip_id": trip_id, "message_id": message_id},
        )


def _flags(countries) -> str:
    """Flags for the countries a trip runs through, longest stretch first."""
    if not countries:
        return ""
    if isinstance(countries, str):
        try:
            countries = json.loads(countries)
        except ValueError:
            return ""
    ordered = sorted(countries.items(), key=lambda item: item[1] or 0, reverse=True)
    return "".join(get_flag_emoji(code) for code, _ in ordered)


def _has_flag(name) -> bool:
    """Whether a station name already begins with a regional-indicator flag."""
    return bool(name) and "\U0001F1E6" <= name[0] <= "\U0001F1FF"


def _time(value, delay_seconds) -> str:
    """"18:43" or "18:43 (+7)" — local clock time, delay in whole minutes."""
    if value is None:
        return ""
    text = value.strftime("%H:%M")
    if delay_seconds:
        minutes = round(delay_seconds / 60)
        if minutes:
            text += f" ({minutes:+d})"
    return text


def format_announcement(trip) -> str:
    """The message body, in the shape the channel already writes by hand."""
    emoji = TYPE_EMOJI.get(trip["trip_type"], "🚆")
    header = " ".join(
        part for part in (trip["operator"], trip["line_name"]) if part
    )
    # Station names are already stored with their country flag ("🇩🇪 Berlin
    # Hbf"), so the header only needs one when the stations carry none.
    flags = _flags(trip["countries"]) if not _has_flag(trip["origin_station"]) else ""

    lines = [f"{emoji} {header}".strip() + (f" {flags}" if flags else "")]
    lines.append(
        f"{_time(trip['start_datetime'], trip['departure_delay'])} {trip['origin_station']}".strip()
    )
    lines.append(
        f"{_time(trip['end_datetime'], trip['arrival_delay'])} {trip['destination_station']}".strip()
    )
    lines.append(f"{SITE_URL}/public/trip/{trip['trip_id']}")
    return "\n".join(lines)


def _card(trip_id):
    """The trip's map card as (filename, png), or None if it cannot be drawn.

    A trip with no routed path — or a renderer that trips over one — should
    still be announced, just without the picture.
    """
    try:
        png = render_trip_card(trip_id)
    except Exception as e:
        logger.warning("Trip card for %s failed: %s", trip_id, e)
        return None
    return (f"trip_{trip_id}.png", png) if png else None


def announce_due_trips(webhook_url, now=None) -> int:
    """One tick: announce every trip that just departed. Returns how many posted."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    users = _opted_in()
    if not users:
        return 0

    posted = 0
    for trip in _due_trips(users.keys(), now):
        if not _claim(trip["trip_id"]):
            continue  # another worker got there first
        username, _ = users[trip["user_id"]]
        message_id = post_webhook_message(
            webhook_url,
            format_announcement(trip),
            username=username,
            file=_card(trip["trip_id"]),
        )
        if message_id:
            _record_message(trip["trip_id"], message_id)
            posted += 1
        elif message_id is False:
            # Discord answered with an error, so nothing was posted: drop the
            # claim and let the next tick try again. The window bounds that at
            # a handful of attempts, and it means a permissions mistake or a
            # rate limit costs a delay rather than the announcement itself.
            _release(trip["trip_id"])
            logger.warning("Trip %s not announced, will retry", trip["trip_id"])
        else:
            # No answer at all: the message may have landed. Keep the claim
            # rather than risk posting the same trip twice.
            logger.warning("Trip %s announcement unconfirmed", trip["trip_id"])
    return posted


def _loop(app, webhook_url):
    while True:
        # Sleep first: the thread starts during import, before the app has
        # finished booting (auth-db columns included).
        time.sleep(POLL_SECONDS)
        try:
            with app.app_context():
                announce_due_trips(webhook_url)
        except Exception as e:
            logger.error("Trip announcer error: %s", e)


def start_trip_announcer(app):
    """Start the announcer thread, unless this is not production."""
    from py.utils import load_config

    # dev runs against a copy of the same trips, with the same users opted in,
    # so a dev instance sharing prod's webhook would post everything twice.
    # Leaving trips_activity out of dev's config is enough on its own; this is
    # the belt to that pair of braces, for the day someone copies a config.
    environment = os.environ.get("ENVIRONMENT")
    if environment != Env.PROD.value:
        logger.info("Trip announcer disabled (ENVIRONMENT=%s, not production)",
                    environment)
        return

    webhook_url = load_config().get("discord", {}).get("trips_activity")
    if not webhook_url:
        logger.info("Trip announcer disabled (no discord.trips_activity webhook)")
        return

    threading.Thread(target=_loop, args=(app, webhook_url), daemon=True).start()
    logger.info("Trip announcer started")
