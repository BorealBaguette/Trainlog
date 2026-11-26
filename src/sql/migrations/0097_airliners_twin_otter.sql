-- The DHC-6 Twin Otter, with a picture in static/images/airliners.
-- base_data/airliners.csv has it too.
--
-- Only for databases that already have airliners. Migrations run before the
-- base data load, which fills the table from the CSV only while it is empty:
-- adding it to a fresh database would stop the other types from loading.
INSERT INTO airliners (iata, manufacturer, model)
SELECT 'DHC6', 'De Havilland Canada', 'DHC-6 Twin Otter'
WHERE EXISTS (SELECT 1 FROM airliners)
    AND NOT EXISTS (SELECT 1 FROM airliners WHERE iata = 'DHC6');
