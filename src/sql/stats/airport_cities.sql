-- City behind an airport code, for the codes a stats payload actually mentions.
-- Airport names are stored on the trip ("Charles de Gaulle International
-- Airport (CDG)"), the city is not, and no part of the name reliably contains
-- it — the charts need it to label a bar in the width they have.
SELECT iata, city
FROM airports
WHERE iata = ANY(:codes)
  AND city IS NOT NULL
  AND city <> ''
