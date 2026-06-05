-- Stats / auxiliary tables migrated from SQLite main.db.

CREATE TABLE percents (
    uid SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    cc TEXT NOT NULL,
    percent INTEGER NOT NULL,
    UNIQUE (username, cc)
);
CREATE INDEX percents_username_idx ON percents (username);

-- Daily currency exchange rates: one row per date, one column per currency
-- (EUR is the base, so it has no column). Column names are kept upper-case and
-- quoted to match the existing query code (py/currency.py, py/update_currency.py).
CREATE TABLE exchanges (
    rate_date DATE PRIMARY KEY,
    "AUD" FLOAT, "BGN" FLOAT, "BRL" FLOAT, "CAD" FLOAT, "CHF" FLOAT,
    "CNY" FLOAT, "CZK" FLOAT, "DKK" FLOAT, "GBP" FLOAT, "HKD" FLOAT,
    "HUF" FLOAT, "IDR" FLOAT, "ILS" FLOAT, "INR" FLOAT, "ISK" FLOAT,
    "JPY" FLOAT, "KRW" FLOAT, "MXN" FLOAT, "MYR" FLOAT, "NOK" FLOAT,
    "NZD" FLOAT, "PHP" FLOAT, "PLN" FLOAT, "RON" FLOAT, "SEK" FLOAT,
    "SGD" FLOAT, "THB" FLOAT, "TRY" FLOAT, "USD" FLOAT, "ZAR" FLOAT,
    "RUB" FLOAT
);

CREATE TABLE daily_active_users (
    date DATE PRIMARY KEY,
    number INTEGER
);

CREATE TABLE fr24_usage (
    uid SERIAL PRIMARY KEY,
    fr24_calls INTEGER DEFAULT 0,
    month_year TEXT NOT NULL,
    username TEXT NOT NULL,
    UNIQUE (username, month_year)
);

CREATE TABLE ai_usage (
    uid SERIAL PRIMARY KEY,
    month_year TEXT NOT NULL,
    username TEXT NOT NULL,
    ai_calls INTEGER DEFAULT 0,
    UNIQUE (username, month_year)
);
