"""Link previews: the PNG a shared Trainlog URL unfurls into.

Discord, Signal and the rest fetch these with no session, so everything served
here is derived from public trips only — both the picture (src.og_card screens
the ids in SQL) and the caption built beside it.
"""

import logging

from flask import Blueprint, Response, redirect, url_for

from src.og_card import render_og_card, render_plan_og_card
from src.pg import pg_session
from src.trip_periods import parse_period, period_trip_ids
from src.trip_selections import is_selection_key, parse_trip_ids, store_trip_ids
from src.trip_stats import trip_stats
from src.users import User
from src.utils import external_url, get_user_id

logger = logging.getLogger(__name__)

og_blueprint = Blueprint("og", __name__)

# Crawlers refetch far more often than the page changes, and the render behind
# this is a Martin round trip.
CACHE_CONTROL = "public, max-age=86400"

# Up to this many trips the ids go in the URL as they are. Past it the selection
# is stored and the URL carries its key instead. Both work; the point of the
# threshold is that a single trip page — by far the most shared link there is —
# should not write a trip_selections row every time someone opens it.
INLINE_IDS = 5


def og_image_url(period=None, tag_uuid=None, trip_ids_param=None, trip_ids=()):
    """The og:image URL for a public trip page, or None when it has no trips.

    A period and a tag are addressed by what they are, so the picture can be
    captioned with their name. A longer selection is addressed by its share key:
    og:image is fetched with GET, never posted to, so a page of two hundred
    trips would otherwise spell every id out in the tag.
    """
    if period is not None:
        return external_url("og.period_image", **period)
    if tag_uuid is not None:
        return external_url("og.tag_image", uuid=tag_uuid)
    if is_selection_key(trip_ids_param or ""):
        return external_url("og.trip_image", trip_ids=trip_ids_param)
    if not trip_ids:
        return None
    if len(trip_ids) <= INLINE_IDS:
        ids = ",".join(str(trip_id) for trip_id in trip_ids)
        return external_url("og.trip_image", trip_ids=ids)
    try:
        key = store_trip_ids(trip_ids)
    except ValueError:
        return None
    return external_url("og.trip_image", trip_ids=key)


def _public_ids(trip_ids):
    """The ids a stranger is allowed to see: public trips of public profiles."""
    if not trip_ids:
        return []
    with pg_session() as pg:
        rows = pg.execute(
            """
            SELECT trip_id, user_id FROM trips
            WHERE trip_id = ANY(:ids) AND visibility = 'public'
            """,
            {"ids": list(trip_ids)},
        ).fetchall()
    public_owners = {
        user.uid
        for user in User.query.filter(
            User.uid.in_({row["user_id"] for row in rows})
        ).all()
        if user.is_public_trips()
    }
    return [row["trip_id"] for row in rows if row["user_id"] in public_owners]


def _caption(trip_ids):
    """(title, subtitle, countries) describing a set of trips."""
    stats = trip_stats(trip_ids)
    with pg_session() as pg:
        ends = pg.execute(
            """
            SELECT origin_station, destination_station
            FROM trips
            WHERE trip_id = ANY(:ids)
            ORDER BY COALESCE(utc_start_datetime, start_datetime) NULLS LAST
            """,
            {"ids": list(trip_ids)},
        ).fetchall()
    title = ""
    if ends:
        title = f"{ends[0]['origin_station']} → {ends[-1]['destination_station']}"
    subtitle = " · ".join(
        part
        for part in (
            f"{stats['trips']} trips" if stats["trips"] > 1 else "",
            f"{round(stats['distance'] / 1000):,} km".replace(",", " "),
        )
        if part
    )
    return title, subtitle, stats["countries"]


def _logo():
    """The fallback whenever there is nothing to draw — never an error page.

    A crawler that is handed a 404 for og:image caches the failure, so a trip
    with no route would keep its blank preview long after it gained one.
    """
    return redirect(url_for("static", filename="images/logo_og.png"))


def _serve(name, trip_ids, title=None):
    """Render these trips, captioned with `title` when the page has a name.

    `name` is the URL segment this was asked for by, and names the cache file.
    """
    ids = _public_ids(trip_ids)
    if not ids:
        return _logo()
    auto_title, subtitle, countries = _caption(ids)
    png = render_og_card(name, ids, title or auto_title, subtitle, countries)
    if png is None:
        return _logo()
    return Response(png, mimetype="image/png", headers={"Cache-Control": CACHE_CONTROL})


@og_blueprint.route("/og/trip/<trip_ids>.png")
def trip_image(trip_ids):
    try:
        ids = parse_trip_ids(trip_ids)
    except ValueError:
        return _logo()
    return _serve(trip_ids, ids)


@og_blueprint.route("/og/tag/<uuid>.png")
def tag_image(uuid):
    """A tag's own picture, captioned with the tag's name rather than its ends."""
    with pg_session() as pg:
        row = pg.execute(
            """
            SELECT tags.name AS name,
                   array_agg(tags_associations.trip_id) AS trip_ids
            FROM tags_associations
            JOIN tags ON tags.uid = tags_associations.tag_id
            WHERE tags.uuid = :uuid
            GROUP BY tags.name
            """,
            {"uuid": uuid},
        ).fetchone()
    if row is None:
        return _logo()
    return _serve(f"tag-{uuid}", row["trip_ids"], title=row["name"])


@og_blueprint.route("/og/<username>/<any(year, month, week):kind>/<period>.png")
def period_image(username, kind, period):
    try:
        start, end = parse_period(kind, period)
    except ValueError:
        return _logo()
    # The same gate the period page itself is behind (@public_required, which
    # asks is_public), not the looser per-trip one. A month drawn as one map is
    # the aggregate view share_level 2 exists to withhold — without this the
    # picture would show what the page it illustrates answers 401 to.
    user = User.query.filter_by(username=username).first()
    if user is None or not user.is_public():
        return _logo()
    user_id = get_user_id(username)
    if user_id is None:
        return _logo()
    return _serve(
        f"{username}-{kind}-{period}",
        period_trip_ids(user_id, start, end),
        title=period,
    )


@og_blueprint.route("/og/plan/<uuid>.png")
def plan_image(uuid):
    """A plan's own picture: its legs, captioned with the plan's name.

    Plan legs have no per-trip visibility, so the gate is the one the shared
    plan page itself applies to a stranger — the owner's profile must be
    public. A logged-in owner sharing a private plan gets the logo here, as the
    crawler fetching this has no session either way.
    """
    with pg_session() as pg:
        plan = pg.execute(
            """
            SELECT p.uid, p.name, p.user_id,
                   GREATEST(
                       COALESCE(p.last_modified, p.created),
                       COALESCE(MAX(pt.last_modified), MAX(pt.created))
                   ) AS version,
                   COUNT(pt.uid) AS trips,
                   COALESCE(SUM(pt.trip_length), 0) AS distance
            FROM plans p
            LEFT JOIN plan_trips pt ON pt.plan_id = p.uid
            WHERE p.uuid = :uuid
            GROUP BY p.uid
            """,
            {"uuid": uuid},
        ).fetchone()
        if plan is None:
            return _logo()
        countries = pg.execute(
            """
            SELECT key AS code,
                   SUM(CASE
                       WHEN jsonb_typeof(value) = 'number' THEN value::numeric
                       ELSE (value->>'elec')::numeric
                            + COALESCE((value->>'nonelec')::numeric, 0)
                   END) AS km
            FROM plan_trips pt, LATERAL jsonb_each(pt.countries::jsonb)
            -- LIKE, not just NOT NULL: a leg whose countries were never
            -- computed stores '', and ''::jsonb is an error, not an empty map.
            WHERE pt.plan_id = :plan_id AND pt.countries LIKE '{%' AND key != 'UN'
            GROUP BY key
            ORDER BY km DESC
            """,
            {"plan_id": plan["uid"]},
        ).fetchall()

    owner = User.query.filter_by(uid=plan["user_id"]).first()
    if owner is None or not owner.is_public_trips():
        return _logo()

    subtitle = " · ".join(
        part
        for part in (
            f"{plan['trips']} trips" if plan["trips"] > 1 else "",
            f"{round((plan['distance'] or 0) / 1000):,} km".replace(",", " "),
        )
        if part
    )
    png = render_plan_og_card(
        uuid,
        plan["name"],
        subtitle,
        [row["code"] for row in countries],
        version=str(plan["version"]),
    )
    if png is None:
        return _logo()
    return Response(png, mimetype="image/png", headers={"Cache-Control": CACHE_CONTROL})
