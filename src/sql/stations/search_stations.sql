-- Search the registry for the autocomplete, alongside Photon, whose index carries only
-- default/de/en/fr — so a name in any other language, or one this application generated or
-- curated, is unsearchable there. See station_aliases in migration 0058.
--
-- Matching is diacritic-folded to line up with station_aliases_alias_trgm_idx (0058), so
-- "munchen" finds "München".
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
       -- Which spelling matched, so the UI can show what the station is otherwise called.
       -- Full names outrank 'official' rows (short_name, loc_name, official_name): those are
       -- abbreviations, and ranking on similarity alone offered "Київ-Пас" as the name of the
       -- station called "Київ-Пасажирський".
       (array_agg(a.alias ORDER BY (a.kind = 'official'),
                                   similarity(station_fold(a.alias), q.needle) DESC))[1]
           AS matched_alias,
       max(similarity(station_fold(a.alias), q.needle)) AS score,
       -- How often this user has been here, counted through the label cache so every spelling
       -- of the station counts. Replaces the client-side boost in util.js, which keyed on an
       -- exact label match and so was zeroed by the name drift this registry exists to fix.
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
-- A prefix match is what someone typing into an autocomplete means; trigram similarity
-- ranks the rest; own visits break ties towards places this user actually goes.
ORDER BY (max(CASE WHEN station_fold(a.alias) LIKE q.needle || '%' THEN 1 ELSE 0 END)) DESC,
         visits DESC,
         score DESC,
         name
LIMIT :limit;
