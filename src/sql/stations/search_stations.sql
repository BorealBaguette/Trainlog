-- Search the registry for the autocomplete. Folded with station_fold() to use the trigram
-- index, so "munchen" finds "München".
WITH q AS (
    SELECT station_fold(:query) AS needle
)
SELECT s.station_id,
       COALESCE(s.curated_name, s.name_intl) AS name,
       s.name_local,
       s.name_intl,
       s.curated_name,
       s.names,
       s.country_code,
       s.effective_lat AS lat,
       s.effective_lng AS lng,
       s.osm_type,
       s.osm_id,
       s.wikidata,
       -- The matched spelling. Short and official names rank last: they are abbreviations.
       (array_agg(a.alias ORDER BY (a.kind = 'official'),
                                   similarity(station_fold(a.alias), q.needle) DESC))[1]
           AS matched_alias,
       max(similarity(station_fold(a.alias), q.needle)) AS score,
       -- How often this user has been here, across every spelling of the station.
       COALESCE((
           SELECT count(*)
           FROM station_labels sl
           JOIN trips t
             ON t.user_id = :user_id
            AND station_type_bucket(t.trip_type) = sl.station_type
            AND (station_normalize(t.origin_station) = sl.normalized
                 OR station_normalize(t.destination_station) = sl.normalized)
           WHERE sl.station_id = s.station_id
       ), 0) AS visits
FROM q
JOIN station_aliases a
  ON station_fold(a.alias) % q.needle
  OR station_fold(a.alias) LIKE q.needle || '%'
JOIN stations s ON s.station_id = a.station_id
WHERE s.station_type = :station_type
  AND s.superseded_by IS NULL
GROUP BY s.station_id, q.needle
ORDER BY (max(CASE WHEN station_fold(a.alias) LIKE q.needle || '%' THEN 1 ELSE 0 END)) DESC,
         visits DESC,
         score DESC,
         name
LIMIT :limit;
