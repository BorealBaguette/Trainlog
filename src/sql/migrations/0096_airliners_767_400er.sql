-- B764 is the 767-400ER: no 767-400 was built without the ER. It is named that
-- way in base_data/airliners.csv too.
UPDATE airliners SET model = '767-400ER' WHERE iata = 'B764' AND model = '767-400';
