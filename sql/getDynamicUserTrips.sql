WITH UTC_Filtered AS (
    SELECT *,
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime
    FROM trip
),
Subquery AS (
    SELECT 
        t.*,
        CASE
            WHEN utc_filtered_start_datetime NOT IN ('1', '-1') 
                 AND utc_filtered_end_datetime NOT IN ('1', '-1') 
                 AND utc_filtered_start_datetime != utc_filtered_end_datetime 
            THEN (julianday(utc_filtered_end_datetime) - julianday(utc_filtered_start_datetime)) * 86400
            ELSE COALESCE(manual_trip_duration, estimated_trip_duration)
        END AS trip_duration_seconds,
        o.short_name AS operator_name,
        time(start_datetime) AS start_time,
        time(end_datetime) AS end_time,
        (SELECT l.logo_url
         FROM operator_logos l
         WHERE l.operator_id = o.uid
           AND (l.effective_date <= t.utc_filtered_start_datetime OR l.effective_date IS NULL OR t.utc_filtered_start_datetime IN (1, -1))
         ORDER BY l.effective_date DESC
         LIMIT 1) AS logo_url
    FROM UTC_Filtered t
    LEFT JOIN operators o ON o.short_name = TRIM(SUBSTR(t.operator, 1, INSTR(t.operator || ',', ',') - 1))
),
FilteredTrips AS (
    SELECT Subquery.*, airliners.*,
           trip_length / trip_duration_seconds AS trip_speed,
           CASE
               WHEN (julianday('now') > julianday(utc_filtered_start_datetime) 
                     OR utc_filtered_start_datetime = -1)
                    AND utc_filtered_start_datetime != 1 THEN 1
               ELSE 0
           END AS 'past',
           CASE
               WHEN julianday('now') <= julianday(utc_filtered_start_datetime) THEN 1
               ELSE 0
           END AS 'plannedFuture',
           CASE
               WHEN utc_filtered_start_datetime = 1 THEN 1
               ELSE 0
           END AS 'future',
           CASE 
               WHEN COUNT(tags_associations.tag_id) = 0 THEN NULL
               ELSE json_group_array(json_object('tag_id', tags_associations.tag_id, 'name', tags.name))
           END AS tags
    FROM Subquery
    LEFT JOIN airliners ON Subquery.material_type = airliners.iata
    LEFT JOIN tags_associations ON Subquery.uid = tags_associations.trip_id
    LEFT JOIN tags ON tag_id = tags.uid
    WHERE Subquery.username = :username
      AND past = :past
      AND (
          remove_diacritics(LOWER(origin_station)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(destination_station)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(operator)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(countries)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(line_name)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(start_datetime)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(end_datetime)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(Subquery.type)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(Subquery.notes)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(Subquery.reg)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(material_type)) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(COALESCE(material_type_advanced, ''))) LIKE remove_diacritics(LOWER(:search)) OR
          remove_diacritics(LOWER(airliners.iata)) LIKE remove_diacritics(LOWER(:search)) OR 
          remove_diacritics(LOWER(airliners.manufacturer)) LIKE remove_diacritics(LOWER(:search)) OR 
          remove_diacritics(LOWER(airliners.model)) LIKE remove_diacritics(LOWER(:search)) OR 
          remove_diacritics(LOWER(tags.name)) LIKE remove_diacritics(LOWER(:search))
      )
    GROUP BY Subquery.uid
)