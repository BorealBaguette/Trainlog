"""A user's tickets: single tickets, passes valid over a period and renewed by hand, gathered
with their renewals, and the trips each one covers."""

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from flask import Blueprint, abort, g, jsonify, render_template, request, session

from py.utils import get_all_countries
from src.currency import get_available_currencies, get_exchange_rate
from src.operators import split_operators
from src.pg import pg_session
from src.sql.tickets import (
    get_operator_logos_query,
    get_tickets_query,
)
from src.trips.edits import attach_ticket_to_trips, delete_ticket_from_db
from src.users import User
from src.utils import get_user_id, lang, login_required

logger = logging.getLogger(__name__)

tickets_blueprint = Blueprint("tickets", __name__)


def _user_currency():
    return User.query.filter_by(username=session["logged_in"]).first().user_currency


def _covered_km(trip, countries):
    """The trip's km within the ticket's countries, or None when it runs in none of them."""
    if not countries:
        return trip["trip_length"] / 1000
    km = [
        sum(value.values()) if isinstance(value, dict) else value
        for country, value in json.loads(trip["countries"] or "{}").items()
        if country in countries
    ]
    return sum(km) / 1000 if km else None


def _countries(ticket):
    return set(ticket["active_countries"].split(",")) if ticket["active_countries"] else None


def _state(ticket, today):
    if ticket["uses"] is not None and ticket["used"] >= ticket["uses"]:
        return "used"
    if not (ticket["valid_from"] or ticket["valid_until"]):
        return "active" if ticket["active"] else "inactive"
    if ticket["valid_from"] and ticket["valid_from"] > today:
        return "upcoming"
    if ticket["valid_until"] and ticket["valid_until"] < today:
        return "expired"
    return "valid"


def _renew_due(username):
    """Auto-renewed tickets that have expired get their successors up to today, at the same price
    and on the same day of the month, for as many months as the ticket lasts; the flag passes to
    the newest, so unticking it there stops the renewals."""
    today = date.today()
    with pg_session() as pg:
        due = pg.execute(
            "SELECT * FROM tickets WHERE username = :username AND auto_renew"
            " AND valid_from IS NOT NULL AND valid_until < CURRENT_DATE",
            {"username": username},
        ).fetchall()
        for ticket in due:
            # Taking the flag first: of two requests renewing at once, only one gets it.
            if pg.execute(
                "UPDATE tickets SET auto_renew = FALSE WHERE uid = :uid AND auto_renew RETURNING uid",
                {"uid": ticket["uid"]},
            ).scalar() is None:
                continue
            anchor = ticket["valid_from"]
            months = max(1, round(((ticket["valid_until"] - anchor).days + 1) / 30.44))
            period, end = 0, ticket["valid_until"]
            # Each period counted from the anchor, not from the one before: no drift over
            # months of different lengths.
            while end < today:
                period += 1
                start = anchor + relativedelta(months=period * months)
                end = anchor + relativedelta(months=(period + 1) * months) - timedelta(days=1)
                pg.execute(
                    "INSERT INTO tickets (name, username, price, currency, purchasing_date,"
                    " valid_from, valid_until, uses, use_minutes, operator, secondary_operators,"
                    " active_countries, notes, auto_renew)"
                    " VALUES (:name, :username, :price, :currency, :start, :start, :end, :uses,"
                    " :use_minutes,"
                    " :operator, :secondary_operators, :active_countries, '', :auto_renew)",
                    {
                        **{key: ticket[key] for key in (
                            "name", "username", "price", "currency", "uses", "use_minutes", "operator",
                            "secondary_operators", "active_countries",
                        )},
                        "start": start,
                        "end": end,
                        "auto_renew": end >= today,
                    },
                )


def _use_numbers(starts, minutes):
    """The use of a clipping card each trip rides on, numbered from 0, for trips departing at
    `starts` in order: given a duration, a trip departing within it of the one that opened the
    current use rides on that use. A trip with no time (unknown, or a date only, saved at second
    1) is a use of its own."""
    numbers, opened, opened_number = [], None, None
    for start in starts:
        timed = start is not None and start.second != 1
        if timed and minutes and opened is not None and start - opened < timedelta(minutes=minutes):
            numbers.append(opened_number)
            continue
        number = max(numbers, default=-1) + 1
        numbers.append(number)
        if timed:
            opened, opened_number = start, number
    return numbers


def _with_uses(ticket, starts):
    """The card's uses counted, and used up, no longer active."""
    ticket["used"] = max(_use_numbers(starts, ticket["use_minutes"]), default=-1) + 1
    ticket["active"] = ticket["active"] and ticket["used"] < ticket["uses"]


def _load_tickets(username):
    """The user's tickets, newest first, renewed when due, with their trips in departure order.
    Each counts its uses, its trips unless it is a clipping card, and a used-up card is no longer
    active."""
    _renew_due(username)
    with pg_session() as pg:
        tickets = [
            dict(row._mapping)
            for row in pg.execute(get_tickets_query(), {"username": username, "uid": None}).fetchall()
        ]
        trips = pg.execute(
            "SELECT trip_id, ticket_id, start_datetime, trip_length, countries FROM trips"
            " WHERE user_id = :user_id AND ticket_id = ANY(:ids)"
            " ORDER BY start_datetime NULLS LAST, trip_id",
            {"user_id": get_user_id(username), "ids": [t["uid"] for t in tickets]},
        ).fetchall()
    by_ticket = defaultdict(list)
    for trip in trips:
        by_ticket[trip["ticket_id"]].append(trip)
    for ticket in tickets:
        ticket_trips = by_ticket[ticket["uid"]]
        if ticket["uses"] is None:
            ticket["used"] = len(ticket_trips)
        else:
            _with_uses(ticket, [t["start_datetime"] for t in ticket_trips])
    return tickets, by_ticket


def _trip_shares(ticket_id):
    """Each of the ticket's trips with its share of the price, in the ticket's currency: an even
    split, or for a clipping card, the price of a use (over all its uses while the card is in
    use, over those it was put to once finished) split among the trips riding on that use."""
    with pg_session() as pg:
        owner = pg.execute("SELECT username FROM tickets WHERE uid = :uid", {"uid": ticket_id}).scalar()
        if owner is None:
            return {}
        ticket = dict(pg.execute(get_tickets_query(), {"username": owner, "uid": ticket_id}).fetchone()._mapping)
        trips = pg.execute(
            "SELECT trip_id, start_datetime FROM trips WHERE ticket_id = :uid"
            " ORDER BY start_datetime NULLS LAST, trip_id",
            {"uid": ticket_id},
        ).fetchall()
    if not trips:
        return {}
    if ticket["uses"] is None:
        return {trip["trip_id"]: ticket["price"] / len(trips) for trip in trips}
    starts = [trip["start_datetime"] for trip in trips]
    numbers = _use_numbers(starts, ticket["use_minutes"])
    _with_uses(ticket, starts)
    in_use = _state(ticket, date.today().isoformat()) in ("valid", "active", "upcoming")
    per_use = ticket["price"] / (ticket["uses"] if in_use else ticket["used"])
    riders = Counter(numbers)
    return {trip["trip_id"]: per_use / riders[number] for trip, number in zip(trips, numbers)}


def ticket_trip_share(ticket_id, trip_id):
    """The trip's share of its ticket's price, in the ticket's currency; cached for the request,
    as a trip list asks once per trip."""
    cache = g.setdefault("_ticket_shares", {})
    if ticket_id not in cache:
        cache[ticket_id] = _trip_shares(ticket_id)
    return cache[ticket_id].get(trip_id, 0)


def _tickets(username):
    """The user's tickets, newest first, each with the trips it covers and its price in the
    user's currency."""
    tickets, by_ticket = _load_tickets(username)

    currency = _user_currency()
    today = date.today().isoformat()
    for ticket in tickets:
        countries = _countries(ticket)
        covered = [(trip["trip_id"], _covered_km(trip, countries)) for trip in by_ticket[ticket["uid"]]]
        covered = [(trip_id, km) for trip_id, km in covered if km is not None]
        ticket["trip_ids"] = [trip_id for trip_id, _ in covered]
        ticket["km"] = sum(km for _, km in covered)
        ticket["user_price"] = get_exchange_rate(
            ticket["price"], ticket["currency"], currency, ticket["purchasing_date"]
        )
        ticket["state"] = _state(ticket, today)
        # A card in use costs its price over all its uses, known from the start; a finished one,
        # over the uses it was put to.
        if ticket["uses"] is None:
            ticket["per_count"] = len(ticket["trip_ids"])
        elif ticket["state"] in ("valid", "active", "upcoming"):
            ticket["per_count"] = ticket["uses"]
        else:
            ticket["per_count"] = ticket["used"]
        ticket["per_trip"] = (
            ticket["user_price"] / ticket["per_count"]
            if ticket["per_count"] and ticket["user_price"] is not None else None
        )
        if ticket["uses"] and ticket["state"] in ("valid", "active"):
            ticket["elapsed"] = round(100 * ticket["used"] / ticket["uses"])
        elif ticket["state"] == "valid" and ticket["valid_from"] and ticket["valid_until"]:
            start, end = date.fromisoformat(ticket["valid_from"]), date.fromisoformat(ticket["valid_until"])
            ticket["elapsed"] = round(100 * ((date.today() - start).days + 1) / ((end - start).days + 1))
    return tickets


def _groups(tickets):
    """Tickets gathered with their renewals: those of one name and operator."""
    groups = defaultdict(list)
    for ticket in tickets:
        operator = re.sub(r"\W+", "", (ticket["operator"] or "").lower())
        groups[(operator, ticket["name"].strip().lower())].append(ticket)

    summaries = []
    for tickets in groups.values():
        spent = sum(t["user_price"] or 0 for t in tickets)
        trips = sum(len(t["trip_ids"]) for t in tickets)
        per_count = sum(t["per_count"] for t in tickets)
        km = sum(t["km"] for t in tickets)
        summaries.append({
            "tickets": tickets,
            "latest": tickets[0],
            "name": tickets[0]["name"],
            "live": any(t["state"] in ("valid", "active", "upcoming") for t in tickets),
            "spent": spent,
            "trips": trips,
            "km": km,
            "per_trip": spent / per_count if per_count else None,
            "per_km": spent / km if km else None,
        })
    return summaries


def _logos(tickets):
    """Each operator the tickets name, with its logo when it has one."""
    names = {
        name
        for t in tickets
        for name in [t["operator"], *split_operators(t["secondary_operators"])]
        if name
    }
    if not names:
        return {}
    with pg_session() as pg:
        rows = pg.execute(get_operator_logos_query(), {"names": list(names)}).fetchall()
    return {row["name"]: row["logo_url"] for row in rows}


def _render(template, username, tickets, **context):
    texts = lang[session["userinfo"]["lang"]]
    return render_template(
        template,
        title=texts["ticket_list"],
        username=username,
        logos=_logos(tickets),
        country_list=get_all_countries(),
        currencyOptions=get_available_currencies(username),
        user_currency=_user_currency(),
        **context,
        **texts,
        **session["userinfo"],
    )


@tickets_blueprint.route("/u/<username>/ticket_list")
@login_required
def ticket_list(username):
    tickets = _tickets(username)
    groups = _groups(tickets)
    # Past groups by the year of their latest ticket, newest first.
    past_years = defaultdict(list)
    for group in groups:
        if not group["live"]:
            latest = group["latest"]
            past_years[(latest["valid_from"] or latest["purchasing_date"])[:4]].append(group)
    return _render(
        "ticket_list.html",
        username,
        tickets,
        live_groups=[g for g in groups if g["live"]],
        past_years=sorted(past_years.items(), reverse=True),
    )


def _group_of(username, ticket_id):
    for group in _groups(_tickets(username)):
        if any(t["uid"] == ticket_id for t in group["tickets"]):
            return group
    abort(404)


@tickets_blueprint.route("/u/<username>/ticket/<int:ticket_id>")
@login_required
def ticket_page(username, ticket_id):
    group = _group_of(username, ticket_id)
    return _render("ticket.html", username, group["tickets"], group=group)


@tickets_blueprint.route("/u/<username>/ticket/<int:ticket_id>/edit_group", methods=["POST"])
@login_required
def edit_group(username, ticket_id):
    """What the tickets of a group share, set on all of them, and their name when one is given;
    those without dates, given a number of days, become valid that long from their purchase."""
    form = request.form
    try:
        days = int(form["validity_days"]) if form.get("validity_days") else None
    except ValueError:
        abort(400)
    if days is not None and days < 1:
        abort(400)
    uids = [t["uid"] for t in _group_of(username, ticket_id)["tickets"]]
    with pg_session() as pg:
        pg.execute(
            "UPDATE tickets SET name = COALESCE(:name, name), operator = :operator, secondary_operators = :secondary_operators,"
            " active_countries = :active_countries,"
            " valid_from = CASE WHEN CAST(:days AS int) IS NOT NULL AND valid_from IS NULL"
            "     AND valid_until IS NULL THEN purchasing_date::date ELSE valid_from END,"
            " valid_until = CASE WHEN CAST(:days AS int) IS NOT NULL AND valid_from IS NULL"
            "     AND valid_until IS NULL THEN purchasing_date::date + CAST(:days AS int) - 1"
            "     ELSE valid_until END"
            " WHERE uid = ANY(:uids) AND username = :username",
            {
                "name": form.get("name", "").strip() or None,
                "operator": form.get("operator", "").strip() or None,
                "secondary_operators": ", ".join(split_operators(form.get("secondary_operators"))) or None,
                "active_countries": ",".join(form.getlist("active_countries[]")) or None,
                "days": days,
                "uids": uids,
                "username": username,
            },
        )
    return jsonify(success=True)


@tickets_blueprint.route("/u/<username>/save_ticket", methods=["POST"])
@login_required
def save_ticket(username):
    form = request.form
    try:
        price = float(form["price"])
        uid = int(form["ticket_id"]) if form.get("ticket_id") else None
        uses = int(form["uses"]) if form.get("uses") else None
        use_minutes = int(form["use_minutes"]) if uses and form.get("use_minutes") else None
    except (KeyError, ValueError):
        abort(400)
    if (uses is not None and uses < 1) or (use_minutes is not None and use_minutes < 1):
        abort(400)
    values = {
        "username": username,
        "uid": uid,
        "name": form.get("name", "").strip(),
        "price": price,
        "currency": form.get("currency"),
        "purchasing_date": form.get("purchasing_date"),
        "valid_from": form.get("valid_from") or None,
        "valid_until": form.get("valid_until") or None,
        "uses": uses,
        "use_minutes": use_minutes,
        "operator": form.get("operator", "").strip() or None,
        "secondary_operators": ", ".join(split_operators(form.get("secondary_operators"))) or None,
        "active_countries": ",".join(form.getlist("active_countries[]")) or None,
        "notes": form.get("notes", ""),
    }
    values["auto_renew"] = bool(form.get("auto_renew") and values["valid_from"] and values["valid_until"])
    if not values["name"] or not values["currency"] or not values["purchasing_date"]:
        abort(400)
    if values["valid_from"] and values["valid_until"] and values["valid_from"] > values["valid_until"]:
        abort(400)

    with pg_session() as pg:
        if uid is None:
            uid = pg.execute(
                "INSERT INTO tickets (name, username, price, currency, purchasing_date, valid_from,"
                " valid_until, uses, use_minutes, operator, secondary_operators, active_countries,"
                " notes, auto_renew)"
                " VALUES (:name, :username, :price, :currency, :purchasing_date, :valid_from,"
                " :valid_until, :uses, :use_minutes, :operator, :secondary_operators,"
                " :active_countries, :notes, :auto_renew)"
                " RETURNING uid",
                values,
            ).scalar()
        elif pg.execute(
            "UPDATE tickets SET name = :name, price = :price, currency = :currency,"
            " purchasing_date = :purchasing_date, valid_from = :valid_from,"
            " valid_until = :valid_until, uses = :uses, use_minutes = :use_minutes,"
            " operator = :operator,"
            " secondary_operators = :secondary_operators,"
            " active_countries = :active_countries, notes = :notes, auto_renew = :auto_renew"
            " WHERE uid = :uid AND username = :username RETURNING uid",
            values,
        ).scalar() is None:
            abort(404)
        if values["auto_renew"]:
            # One renewing ticket per group, or each period would be added twice.
            pg.execute(
                "UPDATE tickets SET auto_renew = FALSE"
                " WHERE username = :username AND uid <> :uid AND lower(trim(name)) = lower(:name)"
                " AND COALESCE(operator_normalize(operator), '') = COALESCE(operator_normalize(:operator), '')",
                {**values, "uid": uid},
            )
    return jsonify(success=True, uid=uid)


@tickets_blueprint.route("/u/<username>/get_all_tickets")
@login_required
def get_all_tickets(username):
    return jsonify(tickets=_load_tickets(username)[0])


@tickets_blueprint.route("/u/<username>/delete_ticket/<int:ticket_id>")
@login_required
def delete_ticket(username, ticket_id):
    success, error = delete_ticket_from_db(username, ticket_id)
    if not success:
        logger.error(error)
        return jsonify(error="An error occurred while deleting the ticket"), 500
    return jsonify(success=True)


@tickets_blueprint.route("/u/<username>/toggle_ticket_active/<int:ticket_id>")
@login_required
def toggle_ticket_active(username, ticket_id):
    with pg_session() as pg:
        pg.execute(
            "UPDATE tickets SET active = NOT active WHERE username = :username AND uid = :uid",
            {"username": username, "uid": ticket_id},
        )
    return jsonify(success=True)


@tickets_blueprint.route("/u/<username>/attachSelected")
@login_required
def attachSelected(username):
    trip_ids = request.args.get("trips")
    ticket_id = request.args.get("ticket_id", type=int)
    if not trip_ids or not ticket_id:
        abort(400)
    success, error = attach_ticket_to_trips(username, ticket_id, trip_ids.split(","))
    if not success:
        logger.error(error)
        return jsonify(error="An error occurred while attaching the ticket"), 500
    return jsonify(success=True)
