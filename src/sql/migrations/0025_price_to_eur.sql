-- Convert an amount to EUR using the closest available daily rate in `exchanges`.
-- PG equivalent of the price_to_eur SQLite helper used for sorting the trip list
-- by price. Exchange columns hold "currency per EUR" rates; EUR is the base.
CREATE OR REPLACE FUNCTION price_to_eur(amount double precision, cur text, d date)
RETURNS double precision AS $$
DECLARE
    rd date;
    rate double precision;
BEGIN
    IF amount IS NULL OR cur IS NULL OR d IS NULL THEN
        RETURN NULL;
    END IF;
    IF cur = 'EUR' THEN
        RETURN amount;
    END IF;

    SELECT COALESCE(
        (SELECT MAX(rate_date) FROM exchanges WHERE rate_date <= d),
        (SELECT MIN(rate_date) FROM exchanges WHERE rate_date >= d)
    ) INTO rd;
    IF rd IS NULL THEN
        RETURN NULL;
    END IF;

    EXECUTE format('SELECT %I FROM exchanges WHERE rate_date = $1', cur)
        INTO rate USING rd;

    IF rate IS NULL OR rate = 0 THEN
        RETURN NULL;
    END IF;
    RETURN round((amount / rate)::numeric, 2);
EXCEPTION WHEN undefined_column THEN
    -- Unsupported currency code (no matching column): treat as unconvertible.
    RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;
