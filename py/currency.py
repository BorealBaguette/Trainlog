import sqlite3


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


def get_exchange_rate(price, base_currency, target_currency, date):
    # Return the unconverted price if the base and target currencies are the same
    if base_currency == target_currency:
        return price

    # Guard against unsupported currencies (e.g. a bogus code saved by the AI
    # importer). These aren't columns in the exchanges table, so SQLite would
    # silently treat the code as a string literal and crash on float().
    supported = {"EUR"} | {c["currency"] for c in get_available_currencies()}
    if base_currency not in supported or target_currency not in supported:
        return None

    # Ensure the price is a float
    price = float(price)
    db_path = "databases/main.db"

    # Attempt to connect to the SQLite database
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
    except sqlite3.Error as e:
        print(f"Database connection error: {e}")
        return None

    # Select the closest date for the rate, either before or after the given date
    date_query = """
        SELECT 
            COALESCE(MAX(rate_date) FILTER (WHERE rate_date <= ?), MIN(rate_date) FILTER (WHERE rate_date >= ?)) AS relevant_date
        FROM exchanges;
    """
    cursor.execute(date_query, (date, date))
    relevant_date = cursor.fetchone()[0]

    if relevant_date:
        # Fetch the rates for the closest date, using SQL parameters to handle the currency codes properly
        rate_query = f"""
            SELECT 
                ({"1" if base_currency == "EUR" else f'"{base_currency}"'}) AS base_rate,
                ({"1" if target_currency == "EUR" else f'"{target_currency}"'}) AS target_rate
            FROM exchanges
            WHERE rate_date = ?;
        """
        cursor.execute(rate_query, (relevant_date,))
        row = cursor.fetchone()

        if row:
            try:
                base_rate, target_rate = (float(row[0]), float(row[1]))
            except (TypeError, ValueError):
                conn.close()
                return None

            if base_currency == "EUR":
                rate = target_rate
            elif target_currency == "EUR":
                rate = 1 / base_rate if base_rate != 0 else None
            else:
                rate = (1 / base_rate * target_rate) if base_rate != 0 else None

            if rate is not None:
                converted_price = round(price * rate, 2)
                conn.close()
                return converted_price
    conn.close()
    return None
