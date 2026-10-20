"""A user's tickets: single tickets, passes valid over a period and renewed by hand, gathered
with their renewals, and the trips each one covers."""

import json
import logging
import re
import unicodedata
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
    get_ticket_query,
    get_tickets_query,
)
from src.trips.edits import attach_ticket_to_trips, delete_ticket_from_db
from src.trips.trip import ticket_id_list
from src.users import User
from src.utils import get_user_id, lang, login_required

logger = logging.getLogger(__name__)

tickets_blueprint = Blueprint("tickets", __name__)


def _words(text):
    """The words of a name, lowercased and without accents."""
    text = unicodedata.normalize("NFKD", text or "").lower()
    return re.findall(r"[^\W_]+", "".join(c for c in text if not unicodedata.combining(c)))


def _logo_at(logos, day):
    """Of logos (effective date, url), newest first, the one in force on a day: the newest
    from before it, or one never dated; without a day, the current one."""
    for effective, url in logos or ():
        if day is None or effective is None or effective.isoformat() <= day:
            return url
    return None


def _product_rows(names=None):
    """Each spelling of a ticket product (the 'ticket' pool of operators), with the product's
    names and every logo, newest first. Given names, those of any operator spelled so
    instead: a ticket product first, then a train operator, then the others."""
    where = "o.operator_type = 'ticket'"
    if names is not None:
        where = (
            "operator_normalize(a.alias) IN"
            " (SELECT operator_normalize(n) FROM unnest(CAST(:names AS text[])) n)"
        )
    with pg_session() as pg:
        return pg.execute(
            "SELECT a.alias, o.operator_id, o.short_name, o.long_name, l.effective_date, l.logo_url"
            " FROM operators o JOIN operator_aliases a ON a.operator_id = o.operator_id"
            " LEFT JOIN operator_logos l ON l.operator_id = o.operator_id"
            f" WHERE {where}"
            " ORDER BY a.alias, o.operator_type <> 'ticket', o.operator_type <> 'operator', o.operator_id,"
            " l.effective_date DESC NULLS LAST, l.uid DESC",
            {"names": names},
        ).fetchall()


def _products(names=None):
    """The ticket products by each of their spellings, written without spaces or punctuation:
    their name and logos, newest first. Given names, the operators of any kind they spell."""
    products = {}
    for row in _product_rows(names):
        key = "".join(_words(row["alias"]))
        if key:
            product = products.setdefault(
                key, {"id": row["operator_id"], "name": row["short_name"], "logos": []}
            )
            # A spelling two operators share: the first one's logos only.
            if row["logo_url"] and product["id"] == row["operator_id"]:
                product["logos"].append((row["effective_date"], row["logo_url"]))
    return products


def _with_operator_products(tickets, products):
    """The ticket products, and the operators the tickets' chosen products name that are
    none: a brand may be any operator."""
    others = {
        t["product"].strip()
        for t in tickets
        if t["product"] and "".join(_words(t["product"])) not in products
    }
    return {**_products(sorted(others)), **products} if others else products


def _product_in(name, products):
    """The product a ticket's name speaks of: the longest run of its words that, written
    together, spells one, so that "Deutschland-Ticket Juli" and "Interrail 2024" are found."""
    words = _words(name)
    for length in range(len(words), 0, -1):
        for start in range(len(words) - length + 1):
            found = products.get("".join(words[start:start + length]))
            if found:
                return found
    return None


def _day(ticket):
    return ticket["valid_from"] or ticket["valid_on"] or ticket["purchasing_date"]


def _with_product(ticket, products):
    """The ticket's product, the one chosen or else the one its name speaks of: a known one
    with its name and the logo it had then, or a free text one as typed."""
    chosen = ticket["product"]
    if chosen is None:
        found = _product_in(ticket["name"], products)
    elif chosen:
        found = products.get("".join(_words(chosen))) or {"name": chosen.strip(), "logos": []}
    else:
        found = None
    ticket["product"] = found["name"] if found else None
    ticket["product_logo"] = _logo_at(found["logos"], _day(ticket)) if found else None


def _product_to_save(product, name, products):
    """The product a form gives: none written is left to the name, unless the name speaks of
    one, which the user has then taken off."""
    product = (product or "").strip()
    if product:
        return product
    return "" if _product_in(name, products) else None


# The shortest pass renewed automatically, by months: four weeks, for one over February.
MIN_RENEW_DAYS = 28


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
    # A single ticket for a given day is valid on that day only; one without dates stays on
    # its manual switch.
    valid_from = ticket["valid_from"] or ticket["valid_on"]
    valid_until = ticket["valid_until"] or ticket["valid_on"]
    if not (valid_from or valid_until):
        return "active" if ticket["active"] else "inactive"
    if valid_from and valid_from > today:
        return "upcoming"
    if valid_until and valid_until < today:
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
                    " product, active_countries, notes, auto_renew)"
                    " VALUES (:name, :username, :price, :currency, :start, :start, :end, :uses,"
                    " :use_minutes,"
                    " :operator, :secondary_operators, :product, :active_countries, '', :auto_renew)",
                    {
                        **{key: ticket[key] for key in (
                            "name", "username", "price", "currency", "uses", "use_minutes", "operator",
                            "secondary_operators", "product", "active_countries",
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
    """The user's tickets, newest first, renewed when due, with their product and their trips
    in departure order. Each counts its uses, its trips unless it is a clipping card, and a
    used-up card is no longer active."""
    _renew_due(username)
    with pg_session() as pg:
        tickets = [
            dict(row._mapping)
            for row in pg.execute(get_tickets_query(), {"username": username, "uid": None}).fetchall()
        ]
        trips = pg.execute(
            "SELECT trip_id, ticket_ids, start_datetime, trip_length, countries FROM trips"
            " WHERE user_id = :user_id AND ticket_ids && CAST(:ids AS integer[])"
            " ORDER BY start_datetime NULLS LAST, trip_id",
            {"user_id": get_user_id(username), "ids": [t["uid"] for t in tickets]},
        ).fetchall()
    by_ticket = defaultdict(list)
    for trip in trips:
        for ticket_id in trip["ticket_ids"]:
            by_ticket[ticket_id].append(trip)
    products = _with_operator_products(tickets, _products()) if tickets else {}
    for ticket in tickets:
        _with_product(ticket, products)
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
            "SELECT trip_id, start_datetime FROM trips WHERE ticket_ids @> ARRAY[CAST(:uid AS integer)]"
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
    """Tickets gathered with their renewals: those of one product whatever their names, or
    else of one name and operator."""
    groups = defaultdict(list)
    for ticket in tickets:
        if ticket["product"]:
            key = ("product", "".join(_words(ticket["product"])))
        else:
            key = (re.sub(r"\W+", "", (ticket["operator"] or "").lower()), ticket["name"].strip().lower())
        groups[key].append(ticket)

    summaries = []
    for tickets in groups.values():
        # What "Edit all" opens on: the latest ticket, the fields its tickets differ in empty.
        mixed = [
            field for field in ("operator", "secondary_operators", "product", "active_countries")
            if len({t[field] or "" for t in tickets}) > 1
        ]
        spent = sum(t["user_price"] or 0 for t in tickets)
        trips = sum(len(t["trip_ids"]) for t in tickets)
        per_count = sum(t["per_count"] for t in tickets)
        km = sum(t["km"] for t in tickets)
        # Who its tickets were bought from, the most used first (the latest on a tie), and
        # who else they were valid with.
        sellers = Counter(t["operator"] for t in tickets if t["operator"])
        operators = sorted(sellers, key=lambda name: -sellers[name])
        # One name per operator however its tickets spell it, as their logos are matched.
        secondary = {"".join(_words(name)): None for name in sellers}
        for t in tickets:
            for name in split_operators(t["secondary_operators"]):
                secondary.setdefault("".join(_words(name)), name)
        secondary = [name for name in secondary.values() if name]
        summaries.append({
            "tickets": tickets,
            "latest": tickets[0],
            "name": tickets[0]["product"] or tickets[0]["name"],
            "mixed_names": len({t["name"].strip().lower() for t in tickets}) > 1,
            # The name all its tickets bear, when the product's title does not already say it.
            "ticket_name": tickets[0]["name"] if tickets[0]["product"]
            and len({t["name"].strip().lower() for t in tickets}) == 1
            and _words(tickets[0]["name"]) != _words(tickets[0]["product"]) else None,
            "operators": [(name, sellers[name]) for name in operators],
            "secondary_operators": secondary,
            "edit": {
                **tickets[0],
                **{field: None for field in mixed},
                "mixed": mixed,
                "mixed_names": len({t["name"].strip().lower() for t in tickets}) > 1,
            },
            # Every country one of its tickets is valid in, in the order they come.
            "countries": ",".join(dict.fromkeys(
                country for t in tickets for country in (t["active_countries"] or "").split(",") if country
            )),
            "live": any(t["state"] in ("valid", "active", "upcoming") for t in tickets),
            "spent": spent,
            "trips": trips,
            "km": km,
            "per_trip": spent / per_count if per_count else None,
            "per_km": spent / km if km else None,
        })
    return summaries


def _logos(tickets):
    """Each operator the tickets name, with its current logo when it has one; and each ticket
    its logo as it was on its date, its product's or else its operator's."""
    names = {
        name
        for t in tickets
        for name in [t["operator"], *split_operators(t["secondary_operators"])]
        if name
    }
    logos = defaultdict(list)
    if names:
        with pg_session() as pg:
            for row in pg.execute(get_operator_logos_query(), {"names": list(names)}).fetchall():
                logos[row["name"]].append((row["effective_date"], row["logo_url"]))
    for ticket in tickets:
        ticket["logo"] = ticket["product_logo"] or _logo_at(logos.get(ticket["operator"]), _day(ticket))
    return {name: _logo_at(eras, None) for name, eras in logos.items()}


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
        min_renew_days=MIN_RENEW_DAYS,
        **context,
        **texts,
        **session["userinfo"],
    )


def trip_ticket_fields(ticket_ids, trip_id, user_currency):
    """A trip's tickets as it is shown: their names, and its share of each, summed in the
    user's currency, and in the tickets' own when they have one. Tickets are read once a
    request, as a trip list asks for the same ones over and over."""
    cache = g.setdefault("_trip_tickets", {})
    missing = [uid for uid in ticket_ids if uid not in cache]
    if missing:
        with pg_session() as pg:
            for row in pg.execute(get_ticket_query(), {"uids": missing}).fetchall():
                cache[row["uid"]] = row
    tickets = [cache[uid] for uid in ticket_ids if uid in cache]
    if not tickets:
        return {}
    shares = [ticket_trip_share(t["uid"], trip_id) for t in tickets]
    in_user = [
        get_exchange_rate(
            price=share, base_currency=t["currency"], target_currency=user_currency,
            date=t["purchasing_date"],
        )
        for share, t in zip(shares, tickets)
    ]
    total = None if None in in_user else sum(in_user)
    currencies = {t["currency"] for t in tickets}
    one_currency = len(currencies) == 1
    return {
        "ticket": " + ".join(t["name"] for t in tickets),
        "ticket_count": len(tickets),
        "ticket_price": sum(shares) if one_currency else total,
        "ticket_currency": currencies.pop() if one_currency else user_currency,
        "ticket_price_in_user_currency": total,
    }


@tickets_blueprint.route("/u/<username>/ticket_list")
@login_required
def ticket_list(username):
    tickets = _tickets(username)
    groups = _groups(tickets)
    # Each year with the groups of its own tickets: a pass used over several years is in each,
    # with what it was that year.
    by_year = defaultdict(list)
    for ticket in tickets:
        by_year[_day(ticket)[:4]].append(ticket)
    years = [(year, _groups(by_year[year])) for year in sorted(by_year, reverse=True)]
    # A year's group whose tickets go on in other years, with the whole of it to go to.
    whole_of = {t["uid"]: group for group in groups for t in group["tickets"]}
    for _, year_groups in years:
        for group in year_groups:
            whole = whole_of[group["latest"]["uid"]]
            if len(whole["tickets"]) > len(group["tickets"]):
                group["whole"] = whole
    return _render(
        "ticket_list.html",
        username,
        tickets,
        live_groups=[g for g in groups if g["live"]],
        years=years,
        all_tickets=tickets,
    )


def _group_of(username, ticket_id):
    for group in _groups(_tickets(username)):
        if any(t["uid"] == ticket_id for t in group["tickets"]):
            return group
    abort(404)


@tickets_blueprint.route("/u/<username>/ticket/<int:ticket_id>")
@login_required
def ticket_page(username, ticket_id):
    """A ticket's group; with ?only, the ticket alone, and with ?year=, the group's tickets of
    that year: what the row followed showed."""
    whole = _group_of(username, ticket_id)
    group = whole
    if "only" in request.args:
        group = _groups([t for t in whole["tickets"] if t["uid"] == ticket_id])[0]
        group["name"] = group["latest"]["name"]
    elif request.args.get("year"):
        year = [t for t in whole["tickets"] if _day(t)[:4] == request.args["year"]]
        if year:
            group = _groups(year)[0]
            group["name"] = f"{group['name']} · {request.args['year']}"
    return _render(
        "ticket.html", username, whole["tickets"], group=group, whole=whole if group is not whole else None
    )


@tickets_blueprint.route("/u/<username>/ticket/<int:ticket_id>/edit_group", methods=["POST"])
@login_required
def edit_group(username, ticket_id):
    """The fields changed in the form (changed[]) set on all the tickets of a group, the others
    left as each ticket has them, and their name when one is given; those without dates, given a
    number of days, become valid that long from the day they were for, or else their purchase."""
    form = request.form
    changed = set(form.getlist("changed[]"))
    try:
        days = int(form["validity_days"]) if form.get("validity_days") else None
    except ValueError:
        abort(400)
    if days is not None and days < 1:
        abort(400)
    tickets = _group_of(username, ticket_id)["tickets"]
    name = form.get("name", "").strip() or None
    products = _products()
    with pg_session() as pg:
        # The product per ticket: their names may each speak of one, or not.
        for ticket in tickets if "product" in changed else ():
            pg.execute(
                "UPDATE tickets SET product = :product WHERE uid = :uid AND username = :username",
                {
                    "product": _product_to_save(form.get("product"), name or ticket["name"], products),
                    "uid": ticket["uid"],
                    "username": username,
                },
            )
        shared = "".join(
            f" {field} = :{field},"
            for field in ("operator", "secondary_operators", "active_countries")
            if field in changed
        )
        pg.execute(
            "UPDATE tickets SET name = COALESCE(:name, name)," + shared +
            " valid_from = CASE WHEN CAST(:days AS int) IS NOT NULL AND valid_from IS NULL"
            "     AND valid_until IS NULL THEN COALESCE(valid_on, purchasing_date::date) ELSE valid_from END,"
            " valid_until = CASE WHEN CAST(:days AS int) IS NOT NULL AND valid_from IS NULL"
            "     AND valid_until IS NULL THEN COALESCE(valid_on, purchasing_date::date) + CAST(:days AS int) - 1"
            "     ELSE valid_until END,"
            # Now a pass, the day it was for is where its period starts.
            " valid_on = CASE WHEN CAST(:days AS int) IS NOT NULL AND valid_from IS NULL"
            "     AND valid_until IS NULL THEN NULL ELSE valid_on END"
            " WHERE uid = ANY(:uids) AND username = :username",
            {
                "name": name,
                "operator": form.get("operator", "").strip() or None,
                "secondary_operators": ", ".join(split_operators(form.get("secondary_operators"))) or None,
                "active_countries": ",".join(form.getlist("active_countries[]")) or None,
                "days": days,
                "uids": [t["uid"] for t in tickets],
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
        "valid_on": form.get("valid_on") or None,
        "uses": uses,
        "use_minutes": use_minutes,
        "operator": form.get("operator", "").strip() or None,
        "secondary_operators": ", ".join(split_operators(form.get("secondary_operators"))) or None,
        "active_countries": ",".join(form.getlist("active_countries[]")) or None,
        "notes": form.get("notes", ""),
    }
    values["auto_renew"] = bool(
        form.get("auto_renew") and values["valid_from"] and values["valid_until"]
        and (date.fromisoformat(values["valid_until"]) - date.fromisoformat(values["valid_from"])).days + 1
        >= MIN_RENEW_DAYS
    )
    if not values["name"] or not values["currency"] or not values["purchasing_date"]:
        abort(400)
    values["product"] = _product_to_save(form.get("product"), values["name"], _products())
    if values["valid_from"] and values["valid_until"] and values["valid_from"] > values["valid_until"]:
        abort(400)

    with pg_session() as pg:
        if uid is None:
            uid = pg.execute(
                "INSERT INTO tickets (name, username, price, currency, purchasing_date, valid_from,"
                " valid_until, valid_on, uses, use_minutes, operator, secondary_operators, product,"
                " active_countries, notes, auto_renew)"
                " VALUES (:name, :username, :price, :currency, :purchasing_date, :valid_from,"
                " :valid_until, :valid_on, :uses, :use_minutes, :operator, :secondary_operators, :product,"
                " :active_countries, :notes, :auto_renew)"
                " RETURNING uid",
                values,
            ).scalar()
        elif pg.execute(
            "UPDATE tickets SET name = :name, price = :price, currency = :currency,"
            " purchasing_date = :purchasing_date, valid_from = :valid_from,"
            " valid_until = :valid_until, valid_on = :valid_on, uses = :uses, use_minutes = :use_minutes,"
            " operator = :operator,"
            " secondary_operators = :secondary_operators, product = :product,"
            " active_countries = :active_countries, notes = :notes, auto_renew = :auto_renew"
            " WHERE uid = :uid AND username = :username RETURNING uid",
            values,
        ).scalar() is None:
            abort(404)
        if values["auto_renew"]:
            # One renewing ticket per group, or each period would be added twice.
            pg.execute(
                "UPDATE tickets SET auto_renew = FALSE"
                " WHERE username = :username AND uid <> :uid AND ("
                "     (lower(trim(name)) = lower(:name) AND COALESCE(operator_normalize(operator), '')"
                "         = COALESCE(operator_normalize(:operator), ''))"
                "     OR (COALESCE(:product, '') <> ''"
                "         AND operator_normalize(product) = operator_normalize(:product)))",
                {**values, "uid": uid},
            )
    return jsonify(success=True, uid=uid)


@tickets_blueprint.route("/u/<username>/ticket_products")
@login_required
def ticket_products(username):
    """The ticket products for the form's picker, shaped as the operator one expects: each
    spelling with its logo, and the product it stands for."""
    rows = _product_rows()
    current = {}
    for row in rows:
        # Newest first: the first logo of a spelling is its current one.
        current.setdefault(row["alias"], row["logo_url"])
    return jsonify(
        operators=current,
        operatorCanonical={row["alias"]: row["short_name"] for row in rows},
        operatorHints={
            row["alias"]: row["long_name"] if row["alias"] == row["short_name"] else row["short_name"]
            for row in rows
            if row["alias"] != row["short_name"] or row["long_name"] != row["short_name"]
        },
    )


@tickets_blueprint.route("/u/<username>/get_all_tickets")
@login_required
def get_all_tickets(username):
    """The user's tickets for the ticket picker, each with its logo."""
    tickets = _load_tickets(username)[0]
    _logos(tickets)
    return jsonify(tickets=tickets)


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
    """The tickets given (ticket_ids, or a single ticket_id) added to the trips', or with
    ?replace, in place of them: none detaching them all."""
    trip_ids = request.args.get("trips")
    ticket_ids = ticket_id_list(request.args.get("ticket_ids", request.args.get("ticket_id")))
    replace = "replace" in request.args
    if not trip_ids or not (ticket_ids or replace):
        abort(400)
    success, error = attach_ticket_to_trips(username, ticket_ids, trip_ids.split(","), replace)
    if not success:
        logger.error(error)
        return jsonify(error="An error occurred while attaching the ticket"), 500
    return jsonify(success=True)
