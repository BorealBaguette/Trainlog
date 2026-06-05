from src.pg import get_or_create_pg_session


def get_available_currencies():
    available_currencies = [
        {"currency": "EUR", "country": "EU"},
        {"currency": "AUD", "country": "AU"},
        {"currency": "BGN", "country": "BG"},
        {"currency": "BRL", "country": "BR"},
        {"currency": "CAD", "country": "CA"},
        {"currency": "CHF", "country": "CH"},
        {"currency": "CNY", "country": "CN"},
        {"currency": "CZK", "country": "CZ"},
        {"currency": "DKK", "country": "DK"},
        {"currency": "GBP", "country": "GB"},
        {"currency": "HKD", "country": "HK"},
        {"currency": "HUF", "country": "HU"},
        {"currency": "IDR", "country": "ID"},
        {"currency": "ILS", "country": "IL"},
        {"currency": "INR", "country": "IN"},
        {"currency": "ISK", "country": "IS"},
        {"currency": "JPY", "country": "JP"},
        {"currency": "KRW", "country": "KR"},
        {"currency": "MXN", "country": "MX"},
        {"currency": "MYR", "country": "MY"},
        {"currency": "NOK", "country": "NO"},
        {"currency": "NZD", "country": "NZ"},
        {"currency": "PHP", "country": "PH"},
        {"currency": "PLN", "country": "PL"},
        {"currency": "RON", "country": "RO"},
        {"currency": "SEK", "country": "SE"},
        {"currency": "SGD", "country": "SG"},
        {"currency": "THB", "country": "TH"},
        {"currency": "TRY", "country": "TR"},
        {"currency": "USD", "country": "US"},
        {"currency": "ZAR", "country": "ZA"},
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

    # Reuse the caller's PG session when provided, otherwise open one. This keeps
    # the helper usable both inside and outside an existing pg_session.
    with get_or_create_pg_session(pg) as session:
        # Select the closest date for the rate, either before or after the given date
        relevant_date = session.execute(
            """
            SELECT COALESCE(
                MAX(rate_date) FILTER (WHERE rate_date <= :date),
                MIN(rate_date) FILTER (WHERE rate_date >= :date)
            ) AS relevant_date
            FROM exchanges
            """,
            {"date": date},
        ).scalar()

        if not relevant_date:
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

    if not row:
        return None

    try:
        base_rate, target_rate = (float(row[0]), float(row[1]))
    except (TypeError, ValueError):
        return None

    if base_currency == "EUR":
        rate = target_rate
    elif target_currency == "EUR":
        rate = 1 / base_rate if base_rate != 0 else None
    else:
        rate = (1 / base_rate * target_rate) if base_rate != 0 else None

    if rate is None:
        return None

    return round(price * rate, 2)
