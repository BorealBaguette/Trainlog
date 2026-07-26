import csv
import io
import zipfile
from datetime import datetime, timedelta

import requests

from src.pg import pg_session


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


def fill_missing_rates(pg):
    """Forward-fill NULL exchange rates in date order (PG equivalent of the old
    ROWID-based fill). Rows are naturally ordered by rate_date (the PK)."""
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
            '''
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
            '''
        )


def get_complete_days(pg):
    """Return the set of rate dates already present in the exchanges table,
    as YYYY-MM-DD strings (to match the generated date series)."""
    results = pg.execute(
        "SELECT DISTINCT rate_date FROM exchanges ORDER BY rate_date DESC"
    ).fetchall()
    return {r[0].strftime("%Y-%m-%d") for r in results}


def download_and_unzip(url):
    """
    Downloads a ZIP file from a URL and unzips it in memory.

    Parameters:
    - url (str): The URL of the ZIP file to download.

    Returns:
    - file_contents (dict): A dictionary with file names as keys and their contents as values.
    """
    # Send a GET request to the URL
    response = requests.get(url)

    # Check if the request was successful
    if response.status_code == 200:
        # Use BytesIO to create a file-like object in memory from the downloaded bytes
        zip_in_memory = io.BytesIO(response.content)

        # Use the zipfile module to read the zip file from the in-memory file-like object
        with zipfile.ZipFile(zip_in_memory, "r") as zip_ref:
            # Get the list of file names contained in the ZIP
            file_names = zip_ref.namelist()

            # Select the first file in the list (or apply any other selection criteria you prefer)
            if file_names:  # Ensure there is at least one file in the ZIP
                file_name = file_names[0]
                file_content = zip_ref.read(file_name).decode("utf-8")
                return file_content
            else:
                raise Exception("The ZIP file is empty.")
    else:
        raise Exception(
            f"Failed to download the ZIP file. HTTP Status Code: {response.status_code}"
        )


# Function to generate a complete list of dates between two dates
def generate_date_series(start_date_str, end_date_str):
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

    current_date = start_date
    while current_date <= end_date:
        yield current_date
        current_date += timedelta(days=1)


def get_rates_from_bottom_in_memory(csv_content, selected_currencies):
    """
    Parses CSV content from a string and gets exchange rates for specific currencies
    from bottom to top.

    Parameters:
    - csv_content (str): The content of the CSV file as a string.
    - selected_currencies (list): A list of currency codes to retrieve rates for.

    Returns:
    - all_rates (dict): A dictionary with dates as keys and another dictionary of currencies
      and their rates as values.
    - all_rates_dates (list): A list of dates for which rates are available.
    """
    all_rates_dates = []
    all_rates = {}
    file_like_object = io.StringIO(csv_content)
    csv_reader = list(csv.DictReader(file_like_object))
    for row in reversed(csv_reader):
        rates = {}
        for currency in selected_currencies:
            rates[currency] = float(row[currency]) if row[currency] != "N/A" else None
        all_rates_dates.append(row["Date"])
        all_rates[row["Date"]] = rates
    return all_rates, all_rates_dates


def process_currency_combinations_daily(all_rates, all_rates_dates):
    with pg_session() as pg:
        time_series = generate_date_series(all_rates_dates[0], all_rates_dates[-1])
        complete_days = get_complete_days(pg)
        filtered_time_series = [
            date
            for date in time_series
            if datetime.strftime(date, "%Y-%m-%d") not in complete_days
        ]

        for date in filtered_time_series:
            rate_date = datetime.strftime(date, "%Y-%m-%d")

            # Determine which date to use for rates based on availability
            if rate_date in all_rates:
                use_date = rate_date
            elif datetime.strftime(date + timedelta(days=-1), "%Y-%m-%d") in all_rates:
                use_date = datetime.strftime(date + timedelta(days=-1), "%Y-%m-%d")
            elif datetime.strftime(date + timedelta(days=-2), "%Y-%m-%d") in all_rates:
                use_date = datetime.strftime(date + timedelta(days=-2), "%Y-%m-%d")
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

        fill_missing_rates(pg)

        last_registered_date = pg.execute(
            "SELECT rate_date FROM exchanges ORDER BY rate_date DESC LIMIT 1"
        ).scalar()
    return last_registered_date


def run_currency_update():
    # List of selected currencies
    selected_currencies = [
        "AUD",
        "BGN",
        "BRL",
        "CAD",
        "CHF",
        "CNY",
        "CZK",
        "DKK",
        "GBP",
        "HKD",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "ISK",
        "JPY",
        "KRW",
        "MXN",
        "MYR",
        "NOK",
        "NZD",
        "PHP",
        "PLN",
        "RON",
        "SEK",
        "SGD",
        "THB",
        "TRY",
        "USD",
        "ZAR",
    ]

    url = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
    unzipped_file = download_and_unzip(url)
    all_rates, all_rates_dates = get_rates_from_bottom_in_memory(
        unzipped_file, selected_currencies
    )
    last_registered_date = process_currency_combinations_daily(
        all_rates, all_rates_dates
    )
    return str(last_registered_date)
