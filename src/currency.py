from flask import g, has_app_context

from src.pg import get_or_create_pg_session


def get_available_currencies():
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
        {"currency": "GGP", "country": "GG"},
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
        {"currency": "IMP", "country": "IM"},
        {"currency": "INR", "country": "IN"},
        {"currency": "IQD", "country": "IQ"},
        {"currency": "IRR", "country": "IR"},
        {"currency": "ISK", "country": "IS"},
        {"currency": "JEP", "country": "JE"},
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
    return available_currencies


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
