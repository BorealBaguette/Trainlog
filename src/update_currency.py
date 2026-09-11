import csv
import io
import logging
from datetime import date, datetime, timedelta

import requests

from src.pg import pg_session

logger = logging.getLogger(__name__)

# Every currency Frankfurter (api.frankfurter.dev) publishes, minus EUR (the table's
# implicit base), the IMF SDR pseudo-currency (XDR), the offshore-yuan duplicate of CNY
# (CNH), and the pre-2018 Mauritanian ouguiya (MRO, superseded by MRU). BGN is
# deliberately absent: Bulgaria adopted the euro in 2026 and Frankfurter dropped it from
# the feed, so its column is left to freeze at its last (fixed peg) value via
# fill_missing_rates.
SELECTED_CURRENCIES = [
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA",
    "ARS", "AUD", "AWG", "AZN", "BAM", "BBD",
    "BDT", "BHD", "BIF", "BMD", "BND", "BOB",
    "BRL", "BSD", "BTN", "BWP", "BYN", "BZD",
    "CAD", "CDF", "CHF", "CLP", "CNY", "COP",
    "CRC", "CUP", "CVE", "CZK", "DJF", "DKK",
    "DOP", "DZD", "EGP", "ERN", "ETB", "FJD",
    "FKP", "GBP", "GEL", "GGP", "GHS", "GIP",
    "GMD", "GNF", "GTQ", "GYD", "HKD", "HNL",
    "HTG", "HUF", "IDR", "ILS", "IMP", "INR",
    "IQD", "IRR", "ISK", "JEP", "JMD", "JOD",
    "JPY", "KES", "KGS", "KHR", "KMF", "KPW",
    "KRW", "KWD", "KYD", "KZT", "LAK", "LBP",
    "LKR", "LRD", "LSL", "LYD", "MAD", "MDL",
    "MGA", "MKD", "MMK", "MNT", "MOP", "MRU",
    "MUR", "MVR", "MWK", "MXN", "MYR", "MZN",
    "NAD", "NGN", "NIO", "NOK", "NPR", "NZD",
    "OMR", "PAB", "PEN", "PGK", "PHP", "PKR",
    "PLN", "PYG", "QAR", "RON", "RSD", "RUB",
    "RWF", "SAR", "SBD", "SCR", "SDG", "SEK",
    "SGD", "SHP", "SLE", "SOS", "SRD", "SSP",
    "STN", "SVC", "SYP", "SZL", "THB", "TJS",
    "TMT", "TND", "TOP", "TRY", "TTD", "TWD",
    "TZS", "UAH", "UGX", "USD", "UYU", "UZS",
    "VES", "VND", "VUV", "WST", "XAF", "XCD",
    "XCG", "XOF", "XPF", "YER", "ZAR", "ZMW",
    "ZWG",
    # Precious metals, spot price per troy ounce — not currencies, but Frankfurter
    # publishes them the same way and it's a fun addition.
    "XAU", "XAG", "XPD", "XPT",
]

FRANKFURTER_RATES_CSV = "https://api.frankfurter.dev/v2/rates.csv"


def _rate_columns(pg):
    """Return the currency column names of the exchanges table (excluding rate_date)."""
    rows = pg.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'exchanges' AND column_name <> 'rate_date'
        """
    ).fetchall()
    return [r[0] for r in rows]


def fill_missing_rates(pg, since=None):
    """Forward-fill NULL exchange rates in date order (PG equivalent of the old
    ROWID-based fill). Rows are naturally ordered by rate_date (the PK).

    `since`, when given, restricts the rows being *written* to rate_date >= since —
    the earlier history is assumed already correct (filled once, at deploy time, from
    base_data/exchanges.csv) — so a small day-to-day top-up doesn't re-scan and
    rewrite the entire multi-decade table on every call. The lookback subquery itself
    is never restricted, so a gap right at the boundary still resolves correctly.
    """
    since_row = " AND t.rate_date >= :since" if since else ""
    since_leading = " AND rate_date >= :since" if since else ""
    params = {"since": since} if since else {}
    for col in _rate_columns(pg):
        # Fill each NULL gap with the most recent earlier non-null value.
        pg.execute(
            f'''
            UPDATE exchanges t
            SET "{col}" = src."{col}"
            FROM exchanges src
            WHERE t."{col}" IS NULL
              AND src."{col}" IS NOT NULL
              AND src.rate_date = (
                  SELECT MAX(p.rate_date) FROM exchanges p
                  WHERE p."{col}" IS NOT NULL AND p.rate_date < t.rate_date
              )
              {since_row}
            ''',
            params,
        )

        # Fill any remaining leading NULLs with the oldest non-null value.
        pg.execute(
            f'''
            UPDATE exchanges
            SET "{col}" = (
                SELECT "{col}" FROM exchanges
                WHERE "{col}" IS NOT NULL ORDER BY rate_date ASC LIMIT 1
            )
            WHERE "{col}" IS NULL
            {since_leading}
            ''',
            params,
        )


def get_complete_days(pg):
    """Return the set of rate dates already present in the exchanges table,
    as YYYY-MM-DD strings (to match the generated date series)."""
    results = pg.execute(
        "SELECT DISTINCT rate_date FROM exchanges ORDER BY rate_date DESC"
    ).fetchall()
    return {r[0].strftime("%Y-%m-%d") for r in results}


def fetch_rates_csv(currencies, start_date, end_date=None):
    """
    Fetch a long-format (date, base, quote, rate) CSV of daily EUR-based rates from
    Frankfurter for the given currencies and date range.

    Returns the CSV content as a string.
    """
    params = {"from": start_date, "quotes": ",".join(currencies)}
    if end_date:
        params["to"] = end_date
    response = requests.get(FRANKFURTER_RATES_CSV, params=params)
    response.raise_for_status()
    return response.text


def parse_long_format_rates(csv_content):
    """
    Parses Frankfurter's long-format CSV (date,base,quote,rate) into the same shape the
    rest of the pipeline expects: a dict of date -> {currency: rate} and the list of
    dates present, oldest first. A currency missing for a given date (e.g. before it
    existed) is simply absent from that date's dict, same as the old ECB "N/A" handling.
    """
    all_rates = {}
    for row in csv.DictReader(io.StringIO(csv_content)):
        all_rates.setdefault(row["date"], {})[row["quote"]] = float(row["rate"])
    all_rates_dates = sorted(all_rates)
    return all_rates, all_rates_dates


def process_currency_combinations_daily(all_rates, all_rates_dates):
    with pg_session() as pg:
        time_series = generate_date_series(all_rates_dates[0], all_rates_dates[-1])
        complete_days = get_complete_days(pg)
        filtered_time_series = [
            d
            for d in time_series
            if datetime.strftime(d, "%Y-%m-%d") not in complete_days
        ]

        for d in filtered_time_series:
            rate_date = datetime.strftime(d, "%Y-%m-%d")

            # Determine which date to use for rates based on availability
            if rate_date in all_rates:
                use_date = rate_date
            elif datetime.strftime(d + timedelta(days=-1), "%Y-%m-%d") in all_rates:
                use_date = datetime.strftime(d + timedelta(days=-1), "%Y-%m-%d")
            elif datetime.strftime(d + timedelta(days=-2), "%Y-%m-%d") in all_rates:
                use_date = datetime.strftime(d + timedelta(days=-2), "%Y-%m-%d")
            else:
                continue  # Skip this date if no rates are available

            rates = all_rates[use_date]
            # Currency codes double as (quoted, case-sensitive) column names.
            col_idents = ", ".join(f'"{currency}"' for currency in rates)
            placeholders = ", ".join(f":{currency}" for currency in rates)
            pg.execute(
                f"INSERT INTO exchanges (rate_date, {col_idents}) "
                f"VALUES (:rate_date, {placeholders}) "
                f"ON CONFLICT (rate_date) DO NOTHING",
                {"rate_date": rate_date, **rates},
            )

        # Only the rows this call could plausibly have touched: everything earlier is
        # assumed already hole-free (see fill_missing_rates' `since` docstring).
        fill_missing_rates(pg, since=all_rates_dates[0])

        last_registered_date = pg.execute(
            "SELECT rate_date FROM exchanges ORDER BY rate_date DESC LIMIT 1"
        ).scalar()
    return last_registered_date


# Function to generate a complete list of dates between two dates
def generate_date_series(start_date_str, end_date_str):
    start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    current_date = start
    while current_date <= end:
        yield current_date
        current_date += timedelta(days=1)


def run_currency_update():
    """
    Top up whatever's new since the last registered rate. Deliberately does NOT fall
    back to pulling full history when the table is empty — that's base_data/exchanges.csv's
    job (loaded once at startup, see src/pg.py's load_exchange_base_data). Refreshing an
    empty table here would mean synchronously downloading and inserting the entire
    multi-decade, 150+ currency series row by row, which is exactly what used to hang
    the app on a fresh/misconfigured database.
    """
    with pg_session() as pg:
        last_registered_date = pg.execute(
            "SELECT rate_date FROM exchanges ORDER BY rate_date DESC LIMIT 1"
        ).scalar()

    if last_registered_date is None:
        logger.error(
            "exchanges table is empty; refusing to backfill full history here — "
            "load base_data/exchanges.csv (src.pg.load_exchange_base_data) first"
        )
        return "exchanges table is empty; load base_data/exchanges.csv first"

    start_date = last_registered_date.strftime("%Y-%m-%d")
    end_date = date.today().strftime("%Y-%m-%d")
    if start_date >= end_date:
        return str(last_registered_date)  # already up to date

    csv_content = fetch_rates_csv(SELECTED_CURRENCIES, start_date, end_date)
    all_rates, all_rates_dates = parse_long_format_rates(csv_content)
    if not all_rates_dates:
        return str(last_registered_date)

    last_registered_date = process_currency_combinations_daily(
        all_rates, all_rates_dates
    )
    return str(last_registered_date)
