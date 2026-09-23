from flask import g, has_app_context

from src.pg import get_or_create_pg_session
from src.utils import get_user_id


def get_available_currencies(username=None):
    # Sourced from Frankfurter (api.frankfurter.dev). BGN stays despite no longer being
    # published (Bulgaria adopted the euro) so old BGN trips keep resolving. XAU/XAG/XPD/XPT
    # are precious metals, not currencies — no real "country", so they get an "emoji" instead.
    available_currencies = [
        {"currency": "AED", "country": "AE"},
        {"currency": "AFN", "country": "AF"},
        {"currency": "ALL", "country": "AL"},
        {"currency": "AMD", "country": "AM"},
        {"currency": "ANG", "country": "CW"},
        {"currency": "AOA", "country": "AO"},
        {"currency": "ARS", "country": "AR"},
        {"currency": "AUD", "country": "AU"},
        {"currency": "AWG", "country": "AW"},
        {"currency": "AZN", "country": "AZ"},
        {"currency": "BAM", "country": "BA"},
        {"currency": "BBD", "country": "BB"},
        {"currency": "BDT", "country": "BD"},
        {"currency": "BGN", "country": "BG"},
        {"currency": "BHD", "country": "BH"},
        {"currency": "BIF", "country": "BI"},
        {"currency": "BMD", "country": "BM"},
        {"currency": "BND", "country": "BN"},
        {"currency": "BOB", "country": "BO"},
        {"currency": "BRL", "country": "BR"},
        {"currency": "BSD", "country": "BS"},
        {"currency": "BTN", "country": "BT"},
        {"currency": "BWP", "country": "BW"},
        {"currency": "BYN", "country": "BY"},
        {"currency": "BZD", "country": "BZ"},
        {"currency": "CAD", "country": "CA"},
        {"currency": "CDF", "country": "CD"},
        {"currency": "CHF", "country": "CH"},
        {"currency": "CLP", "country": "CL"},
        {"currency": "CNY", "country": "CN"},
        {"currency": "COP", "country": "CO"},
        {"currency": "CRC", "country": "CR"},
        {"currency": "CUP", "country": "CU"},
        {"currency": "CVE", "country": "CV"},
        {"currency": "CZK", "country": "CZ"},
        {"currency": "DJF", "country": "DJ"},
        {"currency": "DKK", "country": "DK"},
        {"currency": "DOP", "country": "DO"},
        {"currency": "DZD", "country": "DZ"},
        {"currency": "EGP", "country": "EG"},
        {"currency": "ERN", "country": "ER"},
        {"currency": "ETB", "country": "ET"},
        {"currency": "EUR", "country": "EU"},
        {"currency": "FJD", "country": "FJ"},
        {"currency": "FKP", "country": "FK"},
        {"currency": "GBP", "country": "GB"},
        {"currency": "GEL", "country": "GE"},
        # GGP/IMP/JEP are Channel Islands pseudo-currencies pegged 1:1 to GBP, not
        # real ISO 4217 codes, so Intl.DisplayNames (browser-side) has no name for
        # them — ship an English fallback name for the picker to use instead.
        {"currency": "GGP", "country": "GG", "name": "Guernsey Pound"},
        {"currency": "GHS", "country": "GH"},
        {"currency": "GIP", "country": "GI"},
        {"currency": "GMD", "country": "GM"},
        {"currency": "GNF", "country": "GN"},
        {"currency": "GTQ", "country": "GT"},
        {"currency": "GYD", "country": "GY"},
        {"currency": "HKD", "country": "HK"},
        {"currency": "HNL", "country": "HN"},
        {"currency": "HTG", "country": "HT"},
        {"currency": "HUF", "country": "HU"},
        {"currency": "IDR", "country": "ID"},
        {"currency": "ILS", "country": "IL"},
        {"currency": "IMP", "country": "IM", "name": "Isle of Man Pound"},
        {"currency": "INR", "country": "IN"},
        {"currency": "IQD", "country": "IQ"},
        {"currency": "IRR", "country": "IR"},
        {"currency": "ISK", "country": "IS"},
        {"currency": "JEP", "country": "JE", "name": "Jersey Pound"},
        {"currency": "JMD", "country": "JM"},
        {"currency": "JOD", "country": "JO"},
        {"currency": "JPY", "country": "JP"},
        {"currency": "KES", "country": "KE"},
        {"currency": "KGS", "country": "KG"},
        {"currency": "KHR", "country": "KH"},
        {"currency": "KMF", "country": "KM"},
        {"currency": "KPW", "country": "KP"},
        {"currency": "KRW", "country": "KR"},
        {"currency": "KWD", "country": "KW"},
        {"currency": "KYD", "country": "KY"},
        {"currency": "KZT", "country": "KZ"},
        {"currency": "LAK", "country": "LA"},
        {"currency": "LBP", "country": "LB"},
        {"currency": "LKR", "country": "LK"},
        {"currency": "LRD", "country": "LR"},
        {"currency": "LSL", "country": "LS"},
        {"currency": "LYD", "country": "LY"},
        {"currency": "MAD", "country": "MA"},
        {"currency": "MDL", "country": "MD"},
        {"currency": "MGA", "country": "MG"},
        {"currency": "MKD", "country": "MK"},
        {"currency": "MMK", "country": "MM"},
        {"currency": "MNT", "country": "MN"},
        {"currency": "MOP", "country": "MO"},
        {"currency": "MRU", "country": "MR"},
        {"currency": "MUR", "country": "MU"},
        {"currency": "MVR", "country": "MV"},
        {"currency": "MWK", "country": "MW"},
        {"currency": "MXN", "country": "MX"},
        {"currency": "MYR", "country": "MY"},
        {"currency": "MZN", "country": "MZ"},
        {"currency": "NAD", "country": "NA"},
        {"currency": "NGN", "country": "NG"},
        {"currency": "NIO", "country": "NI"},
        {"currency": "NOK", "country": "NO"},
        {"currency": "NPR", "country": "NP"},
        {"currency": "NZD", "country": "NZ"},
        {"currency": "OMR", "country": "OM"},
        {"currency": "PAB", "country": "PA"},
        {"currency": "PEN", "country": "PE"},
        {"currency": "PGK", "country": "PG"},
        {"currency": "PHP", "country": "PH"},
        {"currency": "PKR", "country": "PK"},
        {"currency": "PLN", "country": "PL"},
        {"currency": "PYG", "country": "PY"},
        {"currency": "QAR", "country": "QA"},
        {"currency": "RON", "country": "RO"},
        {"currency": "RSD", "country": "RS"},
        {"currency": "RUB", "country": "RU"},
        {"currency": "RWF", "country": "RW"},
        {"currency": "SAR", "country": "SA"},
        {"currency": "SBD", "country": "SB"},
        {"currency": "SCR", "country": "SC"},
        {"currency": "SDG", "country": "SD"},
        {"currency": "SEK", "country": "SE"},
        {"currency": "SGD", "country": "SG"},
        {"currency": "SHP", "country": "SH"},
        {"currency": "SLE", "country": "SL"},
        {"currency": "SOS", "country": "SO"},
        {"currency": "SRD", "country": "SR"},
        {"currency": "SSP", "country": "SS"},
        {"currency": "STN", "country": "ST"},
        {"currency": "SVC", "country": "SV"},
        {"currency": "SYP", "country": "SY"},
        {"currency": "SZL", "country": "SZ"},
        {"currency": "THB", "country": "TH"},
        {"currency": "TJS", "country": "TJ"},
        {"currency": "TMT", "country": "TM"},
        {"currency": "TND", "country": "TN"},
        {"currency": "TOP", "country": "TO"},
        {"currency": "TRY", "country": "TR"},
        {"currency": "TTD", "country": "TT"},
        {"currency": "TWD", "country": "TW"},
        {"currency": "TZS", "country": "TZ"},
        {"currency": "UAH", "country": "UA"},
        {"currency": "UGX", "country": "UG"},
        {"currency": "USD", "country": "US"},
        {"currency": "UYU", "country": "UY"},
        {"currency": "UZS", "country": "UZ"},
        {"currency": "VES", "country": "VE"},
        {"currency": "VND", "country": "VN"},
        {"currency": "VUV", "country": "VU"},
        {"currency": "WST", "country": "WS"},
        {"currency": "XAF", "country": "CM"},
        {"currency": "XAG", "country": "", "emoji": "🥈"},
        {"currency": "XAU", "country": "", "emoji": "🥇"},
        {"currency": "XCD", "country": "AG"},
        {"currency": "XCG", "country": "SX"},
        {"currency": "XOF", "country": "CI"},
        {"currency": "XPD", "country": "", "emoji": "🚗"},
        {"currency": "XPF", "country": "PF"},
        {"currency": "XPT", "country": "", "emoji": "💍"},
        {"currency": "YER", "country": "YE"},
        {"currency": "ZAR", "country": "ZA"},
        {"currency": "ZMW", "country": "ZM"},
        {"currency": "ZWG", "country": "ZW"},
    ]

    usage_counts = _get_currency_usage_counts(username)
    if usage_counts:
        available_currencies.sort(
            key=lambda c: (-usage_counts.get(c["currency"], 0), c["currency"])
        )

    return available_currencies


def _get_currency_usage_counts(username):
    """{currency: trip count} for the given user, used to rank the currency
    picker by how often each currency actually gets used. Empty (not an error)
    when no username is given (e.g. a logged-out visitor, or a caller like
    get_exchange_rate() that just needs the supported-codes set), so the list
    just stays alphabetical."""
    if not username or username == "public":
        return {}

    user_id = get_user_id(username)
    if user_id is None:
        return {}

    with get_or_create_pg_session(None) as session:
        rows = session.execute(
            """
            SELECT currency, COUNT(*) AS uses
            FROM trips
            WHERE user_id = :user_id AND currency IS NOT NULL
            GROUP BY currency
            """,
            {"user_id": user_id},
        ).fetchall()

    return {row[0]: row[1] for row in rows}


def get_exchange_rate(price, base_currency, target_currency, date, pg=None):
    # Return the unconverted price if the base and target currencies are the same
    if base_currency == target_currency:
        return price

    # Guard against unsupported currencies (e.g. a bogus code saved by the AI
    # importer). These aren't columns in the exchanges table, so a quoted code
    # would otherwise reference a non-existent column.
    supported = {"EUR"} | {c["currency"] for c in get_available_currencies()}
    if base_currency not in supported or target_currency not in supported:
        return None

    # Ensure the price is a float
    price = float(price)

    # The rate for a (base, target, date) triple is constant within a request,
    # but callers convert per trip/ticket — cache the rate on g so repeated
    # dates cost one lookup. Scripts run without an app context and skip it.
    cache_key = (base_currency, target_currency, str(date))
    cache = None
    if has_app_context():
        cache = getattr(g, "_fx_rate_cache", None)
        if cache is None:
            cache = g._fx_rate_cache = {}
        if cache_key in cache:
            rate = cache[cache_key]
            return round(price * rate, 2) if rate is not None else None

    # Reuse the caller's PG session when provided, otherwise open one. This keeps
    # the helper usable both inside and outside an existing pg_session.
    with get_or_create_pg_session(pg) as session:
        # Select the closest date for the rate, either before or after the given date
        # Two plain MIN/MAX subqueries so each is answered from the rate_date
        # PK index; the FILTER-aggregate form forced a full-table scan per call.
        relevant_date = session.execute(
            """
            SELECT COALESCE(
                (SELECT MAX(rate_date) FROM exchanges WHERE rate_date <= :date),
                (SELECT MIN(rate_date) FROM exchanges WHERE rate_date >= :date)
            ) AS relevant_date
            """,
            {"date": date},
        ).scalar()

        if not relevant_date:
            if cache is not None:
                cache[cache_key] = None
            return None

        # Currency codes map to column names; they are validated against `supported`
        # above so interpolating them here is safe.
        base_expr = "1" if base_currency == "EUR" else f'"{base_currency}"'
        target_expr = "1" if target_currency == "EUR" else f'"{target_currency}"'
        row = session.execute(
            f"""
            SELECT ({base_expr}) AS base_rate, ({target_expr}) AS target_rate
            FROM exchanges
            WHERE rate_date = :rate_date
            """,
            {"rate_date": relevant_date},
        ).fetchone()

    rate = None
    if row:
        try:
            base_rate, target_rate = (float(row[0]), float(row[1]))
        except (TypeError, ValueError):
            base_rate = target_rate = None
        if base_rate is not None:
            if base_currency == "EUR":
                rate = target_rate
            elif target_currency == "EUR":
                rate = 1 / base_rate if base_rate != 0 else None
            else:
                rate = (1 / base_rate * target_rate) if base_rate != 0 else None

    if cache is not None:
        cache[cache_key] = rate

    if rate is None:
        return None

    return round(price * rate, 2)


def get_currency_leaderboard(pg=None):
    """Aggregate every priced trip by currency: usage share plus totals/averages
    in the original currency and converted to EUR at each expense's own purchase
    date (a trip's purchase_date, or a ticket's purchasing_date), so a 5-year-old
    TRY price isn't valued at today's rate.
    Flag/name display is left to the frontend (same Intl.DisplayNames + getFlagEmoji
    logic already used by the picker on this page)."""
    with get_or_create_pg_session(pg) as session:
        # A trip's spend can come from its own price and/or from a ticket that
        # covers several trips (its price is split evenly across them). When both
        # exist in the same currency they're the same expense recorded twice, so
        # only the ticket share counts; in different currencies they're treated as
        # two separate charges (e.g. a pass plus a separately-paid seat fee) and
        # both count.
        # Sums are grouped per (currency, day) before calling price_to_eur so the
        # plpgsql rate lookup runs once per distinct day rather than once per row.
        rows = session.execute(
            """
            WITH base AS (
                SELECT
                    t.trip_id,
                    t.price AS trip_price,
                    t.currency AS trip_currency,
                    COALESCE(t.purchase_date, t.start_datetime, t.created)::date AS trip_date,
                    tk.price AS ticket_price,
                    tk.currency AS ticket_currency,
                    tk.purchasing_date::date AS ticket_date,
                    COUNT(*) OVER (PARTITION BY tk.uid) AS ticket_trip_count
                FROM trips t
                LEFT JOIN tickets tk
                    ON tk.uid = t.ticket_id
                    AND tk.price IS NOT NULL AND tk.price != 0
                    AND tk.currency IS NOT NULL AND tk.currency != ''
            ),
            priced AS (
                SELECT ticket_currency AS currency, ticket_date AS d,
                       ticket_price / ticket_trip_count AS price
                FROM base
                WHERE ticket_currency IS NOT NULL

                UNION ALL

                SELECT trip_currency AS currency, trip_date AS d, trip_price AS price
                FROM base
                WHERE trip_price IS NOT NULL AND trip_price != 0
                    AND trip_currency IS NOT NULL AND trip_currency != ''
                    AND (ticket_currency IS NULL OR ticket_currency != trip_currency)
            ),
            per_day AS (
                SELECT currency, d, COUNT(*) AS n, SUM(price) AS total,
                       price_to_eur(SUM(price), currency, d) AS total_eur
                FROM priced
                GROUP BY currency, d
            )
            SELECT currency, SUM(n)::int AS trip_count, SUM(total) AS total_price,
                   SUM(total) / SUM(n) AS avg_price, SUM(total_eur) AS total_price_eur
            FROM per_day
            GROUP BY currency
            ORDER BY trip_count DESC
            """
        ).fetchall()

    total_trips = sum(row.trip_count for row in rows)

    leaderboard = []
    for row in rows:
        total_price_eur = (
            round(row.total_price_eur, 2) if row.total_price_eur is not None else None
        )
        leaderboard.append(
            {
                "currency": row.currency,
                # Full precision kept (not rounded to 2dp here) so tiny shares
                # don't all collapse to a wall of "0.00%" — the frontend picks
                # a value-aware number of decimals per row.
                "percentage": (row.trip_count / total_trips * 100) if total_trips else 0,
                "trip_count": row.trip_count,
                "total_price": round(row.total_price, 2),
                "avg_price": round(row.avg_price, 2),
                "total_price_eur": total_price_eur,
                "avg_price_eur": round(total_price_eur / row.trip_count, 2)
                if total_price_eur is not None
                else None,
            }
        )

    total_price_eur_sum = sum(
        row["total_price_eur"] for row in leaderboard if row["total_price_eur"] is not None
    )
    avg_price_eur_overall = round(total_price_eur_sum / total_trips, 2) if total_trips else None

    return {
        "leaderboard": leaderboard,
        "total_trips": total_trips,
        "total_price_eur_sum": round(total_price_eur_sum, 2),
        "avg_price_eur_overall": avg_price_eur_overall,
    }
