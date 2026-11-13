-- Twelve more civil types, with pictures in static/images/airliners: A310,
-- BAe 146-100, 707, 727, Dornier 328, Fokker 70, DC-9, DC-10, MD-11, MD-83,
-- MD-87 and MD-90. base_data/airliners.csv has them too.
--
-- Only for databases that already have airliners. Migrations run before the
-- base data load, which fills the table from the CSV only while it is empty:
-- adding these to a fresh database would stop the other types from loading.
INSERT INTO airliners (iata, manufacturer, model)
SELECT v.iata, v.manufacturer, v.model
FROM (VALUES
    ('A310', 'Airbus', 'A310'),
    ('B461', 'British Aerospace', '146-100'),
    ('B703', 'Boeing', '707-300'),
    ('B722', 'Boeing', '727-200'),
    ('D328', 'Dornier', '328'),
    ('F70', 'Fokker', 'F70'),
    ('DC93', 'McDonnell Douglas', 'DC-9-30'),
    ('DC10', 'McDonnell Douglas', 'DC-10'),
    ('MD11', 'McDonnell Douglas', 'MD-11'),
    ('MD83', 'McDonnell Douglas', 'MD-83'),
    ('MD87', 'McDonnell Douglas', 'MD-87'),
    ('MD90', 'McDonnell Douglas', 'MD-90')
) AS v (iata, manufacturer, model)
WHERE EXISTS (SELECT 1 FROM airliners)
    AND NOT EXISTS (SELECT 1 FROM airliners a WHERE a.iata = v.iata);
