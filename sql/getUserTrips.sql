WITH UTC_Filtered AS (
    SELECT *, 
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(
            datetime(
                utc_start_datetime,
                printf('%+d seconds', COALESCE(departure_delay, 0))
            ),
            start_datetime
        ) AS actual_start_datetime
    FROM trip
)

SELECT *,
CASE
	WHEN  
		(
			julianday('now') > julianday(actual_start_datetime) 
			OR utc_filtered_start_datetime = -1
		)
		AND utc_filtered_start_datetime != 1
	THEN 1
	ELSE 0
END AS 'past',
CASE
	WHEN julianday('now') <= julianday(actual_start_datetime)
	THEN 1
	ELSE 0 
END AS 'plannedFuture',
CASE
	WHEN utc_filtered_start_datetime=1
	THEN 1
	ELSE 0 
END AS 'future',
CASE 
	WHEN COUNT(tags_associations.tag_id) = 0 THEN NULL
	ELSE json_group_array(
		json_object('tag_id', tags_associations.tag_id, 'name', tags.name)
	)
END AS tags
FROM UTC_Filtered 
LEFT JOIN airliners ON UTC_Filtered.material_type = airliners.iata
LEFT JOIN tags_associations ON UTC_Filtered.uid = tags_associations.trip_id
LEFT JOIN tags ON tag_id = tags.uid
WHERE UTC_Filtered.username = :username 
AND UTC_Filtered.type in ('train', 'bus', 'air', 'helicopter', 'ferry')
GROUP BY UTC_Filtered.uid, airliners.iata
ORDER BY utc_filtered_start_datetime = 1 DESC, utc_filtered_start_datetime DESC, uid DESC
