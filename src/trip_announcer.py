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

Posting by hand
---------------
The announcer only posts inside a short window at departure, so a trip logged
late, or one that fell in an outage, would never make it out. ``post_trip_now``
lets the owner post it themselves from the trips table for as long as they are
travelling (an hour either side of the trip, ``MANUAL_POST_GRACE``). Bounding it
that way keeps the channel meaning "somebody is on their way" and makes it
impossible to empty a whole trip history into it. The claim row is the same one
the announcer takes, so a trip posted by hand is not posted again at departure.

Taking a post down again
------------------------
A trip posted while it was public by mistake says where somebody is, so the
owner can delete their own post from the trips table (``retract_announcement``,
reached from the actions menu). The claim row stays behind with its
``message_id`` cleared: a trip whose post was taken down is never announced a
second time, which is what someone who just took one down would expect.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from py.utils import get_flag_emoji
from src.consts import Env
from src.discord_bot import (
    delete_webhook_message,
    guild_display_name,
    post_webhook_message,
)
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

# How far either side of a trip its owner may still post it by hand. Wide
# enough for "about to board" and for remembering halfway through a night
# train; narrow enough that only a trip actually being travelled can be posted.
MANUAL_POST_GRACE = timedelta(hours=1)

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


def announced_trip_ids(user_id):
    """The user's trips whose Discord post is still up.

    Read once when the trips table is rendered, to decide which rows offer to
    take their post down. A trip whose post was never confirmed (Discord never
    answered, so its id was never learned) has no id to delete by and is not
    listed.
    """
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT a.trip_id
            FROM trip_announcements a
            JOIN trips t ON t.trip_id = a.trip_id
            WHERE t.user_id = :user_id AND a.message_id IS NOT NULL
            """,
            {"user_id": user_id},
        ).fetchall()
    return [row["trip_id"] for row in rows]


# A trip's own clock, delays included, in the same naive-UTC terms as
# utc_start_datetime — the shape get_current_trip.sql already uses to decide
# what someone is travelling on right now.
_TRIP_WINDOW = """
    :now >= COALESCE(t.utc_start_datetime, t.start_datetime)
            + make_interval(secs => COALESCE(t.departure_delay, 0)) - :grace
AND :now <= COALESCE(t.utc_end_datetime, t.end_datetime)
            + make_interval(secs => COALESCE(t.arrival_delay, 0)) + :grace
"""

# Public, not a project, nothing of it in the channel, and being travelled:
# everything that has to be true before a trip may be posted by hand.
#
# Keyed on the message id rather than on the claim row, so a trip whose post was
# taken down can be posted again — the row stays behind either way, which is
# what stops the announcer posting it a second time on its own.
_POSTABLE = f"""
    FROM trips t
    LEFT JOIN trip_announcements a ON a.trip_id = t.trip_id
    WHERE t.user_id = :user_id
      AND t.visibility = 'public'
      AND NOT COALESCE(t.is_project, FALSE)
      AND a.message_id IS NULL
      AND {_TRIP_WINDOW}
"""


# How soon a trip may be posted again after an attempt. Long enough that a
# double-click cannot post twice, short enough not to be felt after a failure.
_REPOST_COOLDOWN = timedelta(seconds=30)


def _claim_for_hand_post(trip_id, now) -> bool:
    """Take responsibility for posting this trip by hand. True in one caller.

    Claims a trip that was never announced (the INSERT) and one whose post was
    taken down (the UPDATE), but never one that is in the channel right now: a
    row with a message id fails the WHERE and nothing is returned. Restamping
    ``announced`` is what makes the claim exclusive — a second click, arriving
    while the first is still talking to Discord, finds the row too fresh and is
    turned away rather than posting the trip twice.
    """
    with pg_session() as pg:
        row = pg.execute(
            """
            INSERT INTO trip_announcements (trip_id, announced)
            VALUES (:trip_id, :now)
            ON CONFLICT (trip_id) DO UPDATE SET announced = :now
            WHERE trip_announcements.message_id IS NULL
              AND trip_announcements.announced < :cutoff
            RETURNING trip_id
            """,
            {"trip_id": trip_id, "now": now, "cutoff": now - _REPOST_COOLDOWN},
        ).fetchone()
    return row is not None


def _window_params(user_id):
    return {
        "user_id": user_id,
        "now": datetime.now(timezone.utc).replace(tzinfo=None),
        "grace": MANUAL_POST_GRACE,
    }


def postable_trip_ids(user_id):
    """The user's trips that may be posted by hand right now.

    Read when the trips table is rendered, so only those rows offer to post.
    Nearly always empty or a single trip — someone is on one train at a time.
    """
    with pg_session() as pg:
        rows = pg.execute(
            "SELECT t.trip_id" + _POSTABLE, _window_params(user_id)
        ).fetchall()
    return [row["trip_id"] for row in rows]


def is_postable(trip_id, user_id) -> bool:
    """Whether this one trip may be posted by hand right now.

    The page asks after a post is taken down, to decide whether the row offers
    to put it back — the trip may have travelled out of its window in the
    meantime.
    """
    with pg_session() as pg:
        row = pg.execute(
            "SELECT t.trip_id" + _POSTABLE + " AND t.trip_id = :trip_id",
            dict(_window_params(user_id), trip_id=trip_id),
        ).fetchone()
    return row is not None


def post_trip_now(trip_id, user_id, username, discord_id=None):
    """Post one trip to the channel now, at its owner's request.

    Returns (posted, reason). The conditions are checked here rather than
    trusted from the page: what the browser was told is postable may have
    stopped being so, or never have been.
    """
    params = dict(_window_params(user_id), trip_id=trip_id)
    with pg_session() as pg:
        trip = pg.execute(
            """
            SELECT t.trip_id, t.user_id, t.operator, t.line_name, t.trip_type,
                   t.origin_station, t.destination_station, t.countries,
                   t.start_datetime, t.end_datetime,
                   t.departure_delay, t.arrival_delay
            """
            + _POSTABLE
            + " AND t.trip_id = :trip_id",
            params,
        ).fetchone()
    if trip is None:
        logger.info("Trip %s is not postable by hand", trip_id)
        return False, "not_postable"

    webhook_url, reason = _webhook_url()
    if not webhook_url:
        logger.warning("Trip %s not posted (%s)", trip_id, reason)
        return False, "no_webhook"

    if not _claim_for_hand_post(trip_id, params["now"]):
        # The announcer got there between the check and here, or this is a
        # second click on the same button.
        return False, "not_postable"
    # release_on_error=False: the claim row doubles as the record that this trip
    # was posted by hand once, and a row with no message id is postable again —
    # so a failure needs nothing undone, and dropping the row would let the
    # announcer post the trip by itself after all.
    if _announce(webhook_url, trip, username, discord_id, release_on_error=False):
        logger.info("Trip %s posted to Discord at its owner's request", trip_id)
        return True, "ok"
    return False, "discord_error"


def retract_announcement(trip_id) -> bool:
    """Take a trip's post out of the channel. True once it is gone.

    A trip that was never announced, or whose post is already down, is left
    alone and counts as done.
    """
    with pg_session() as pg:
        row = pg.execute(
            "SELECT message_id FROM trip_announcements WHERE trip_id = :trip_id",
            {"trip_id": trip_id},
        ).fetchone()
    if row is None or row["message_id"] is None:
        return True

    webhook_url, reason = _webhook_url()
    if not webhook_url:
        logger.warning("Announcement of trip %s left in place (%s)", trip_id, reason)
        return False

    if not delete_webhook_message(webhook_url, row["message_id"]):
        logger.warning("Trip %s announcement could not be deleted", trip_id)
        return False

    # The row stays, so _due_trips keeps skipping this trip and it is never
    # announced again. Clearing the id is what marks the post as gone; nothing
    # re-claims a trip that already has a row, so it cannot be confused with a
    # claim that has yet to be posted (see _release).
    with pg_session() as pg:
        pg.execute(
            "UPDATE trip_announcements SET message_id = NULL WHERE trip_id = :trip_id",
            {"trip_id": trip_id},
        )
    logger.info("Trip %s announcement deleted at its owner's request", trip_id)
    return True


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


def format_announcement(trip, has_card=False) -> str:
    """The message body, in the shape the channel already writes by hand.

    With a card attached the link is wrapped in <> so Discord does not unfurl
    it: since trip pages carry a map as their og:image, the same picture would
    otherwise appear twice in one message.
    """
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
    url = f"{SITE_URL}/public/trip/{trip['trip_id']}"
    lines.append(f"<{url}>" if has_card else url)
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


def _poster(username, discord_id):
    """The name and face to post a trip under, as (name, avatar url).

    People in the channel know each other by their server nickname, which is
    often nothing like either the Trainlog username or the Discord account
    name, so that is what a post made on someone's behalf carries. The Trainlog
    username stands in when the member cannot be looked up — the post still
    says who travelled, which is the point of it.
    """
    if not discord_id:
        return username, None
    name, avatar = guild_display_name(discord_id)
    return name or username, avatar


def _announce(webhook_url, trip, username, discord_id=None, release_on_error=True) -> bool:
    """Post one already-claimed trip. True if it made it into the channel."""
    card = _card(trip["trip_id"])
    name, avatar = _poster(username, discord_id)
    message_id = post_webhook_message(
        webhook_url,
        format_announcement(trip, has_card=card is not None),
        username=name,
        file=card,
        avatar_url=avatar,
    )
    if message_id:
        _record_message(trip["trip_id"], message_id)
        return True
    if message_id is False:
        # Discord answered with an error, so nothing was posted: drop the claim
        # and let the next tick try again. The window bounds that at a handful
        # of attempts, and it means a permissions mistake or a rate limit costs
        # a delay rather than the announcement itself.
        if release_on_error:
            _release(trip["trip_id"])
        logger.warning("Trip %s not announced, will retry", trip["trip_id"])
    else:
        # No answer at all: the message may have landed. Keep the claim rather
        # than risk posting the same trip twice.
        logger.warning("Trip %s announcement unconfirmed", trip["trip_id"])
    return False


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
        username, discord_id = users[trip["user_id"]]
        if _announce(webhook_url, trip, username, discord_id):
            posted += 1
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


def _webhook_url():
    """The configured channel webhook as (url, None), or (None, why not).

    Whoever put a webhook in the config chose a channel deliberately, so the
    two things a person asks for by hand — posting one trip, taking one post
    down — are allowed wherever one is configured, dev included. Only the
    unattended announcer is held to production as well (see
    start_trip_announcer): it is the one that posts without being asked.
    """
    from py.utils import load_config

    webhook_url = load_config().get("discord", {}).get("trips_activity")
    if not webhook_url:
        return None, "no discord.trips_activity webhook"
    return webhook_url, None


def start_trip_announcer(app):
    """Start the announcer thread, unless this is not production."""
    # dev runs against a copy of the same trips, with the same users opted in,
    # so a dev instance sharing prod's webhook would post everything twice.
    # Leaving trips_activity out of dev's config is enough on its own; this is
    # the belt to that pair of braces, for the day someone copies a config.
    environment = os.environ.get("ENVIRONMENT")
    if environment != Env.PROD.value:
        logger.info(
            "Trip announcer disabled (ENVIRONMENT=%s, not production)", environment
        )
        return

    webhook_url, reason = _webhook_url()
    if not webhook_url:
        logger.info("Trip announcer disabled (%s)", reason)
        return

    threading.Thread(target=_loop, args=(app, webhook_url), daemon=True).start()
    logger.info("Trip announcer started")
