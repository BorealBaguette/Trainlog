-- PostgreSQL train-station autocomplete (case-insensitive via ILIKE).
SELECT * FROM train_stations
WHERE name ILIKE :searchPatternAnywhere
   OR latin_name ILIKE :searchPatternAnywhere
   OR city ILIKE :searchPatternAnywhere
   OR latin_city ILIKE :searchPatternAnywhere
   OR processed_name ILIKE :searchPatternAnywhere
ORDER BY
    CASE
        WHEN processed_name ILIKE :searchPatternStart THEN 1
        WHEN processed_name ILIKE :searchPatternAnywhere THEN 2
        WHEN name ILIKE :searchPatternStart THEN 3
        WHEN name ILIKE :searchPatternAnywhere THEN 4
        WHEN latin_city ILIKE :searchPatternStart THEN 5
        WHEN latin_city ILIKE :searchPatternAnywhere THEN 6
        WHEN city ILIKE :searchPatternStart THEN 7
        WHEN city ILIKE :searchPatternAnywhere THEN 8
        ELSE 10
    END
LIMIT 10
