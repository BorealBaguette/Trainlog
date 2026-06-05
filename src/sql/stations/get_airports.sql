-- PostgreSQL airport autocomplete (case-insensitive via ILIKE).
SELECT * FROM airports
WHERE iata ILIKE :searchPattern
   OR name ILIKE :searchPattern
   OR ident ILIKE :searchPattern
   OR city ILIKE :searchPattern
ORDER BY (iata ILIKE :searchPattern) DESC, (ident ILIKE :searchPattern) DESC
LIMIT 10
