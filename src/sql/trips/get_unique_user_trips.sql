WITH RankedTrips AS (
    SELECT
        t.*,
        ST_AsGeoJSON(p.path) as path,
        EXTRACT(YEAR FROM COALESCE(t.utc_start_datetime, t.start_datetime)) AS trip_year,
        CASE
            WHEN t.is_project = true THEN 'future'
            WHEN t.start_datetime IS NULL OR t.end_datetime IS NULL THEN 'future'
            WHEN NOW() > COALESCE(t.utc_end_datetime, t.end_datetime) THEN 'past'
            WHEN NOW() BETWEEN COALESCE(t.utc_start_datetime, t.start_datetime)
                            AND COALESCE(t.utc_end_datetime, t.end_datetime) THEN 'current'
            ELSE 'plannedFuture'
        END AS time_status,
        ROW_NUMBER() OVER (
            PARTITION BY
                t.origin_station,
                t.destination_station,
                t.trip_length,
                EXTRACT(YEAR FROM COALESCE(t.utc_start_datetime, t.start_datetime)),
                CASE
                    WHEN t.is_project = true THEN 'future'
                    WHEN t.start_datetime IS NULL OR t.end_datetime IS NULL THEN 'future'
                    WHEN NOW() > COALESCE(t.utc_end_datetime, t.end_datetime) THEN 'past'
                    WHEN NOW() BETWEEN COALESCE(t.utc_start_datetime, t.start_datetime)
                                    AND COALESCE(t.utc_end_datetime, t.end_datetime) THEN 'current'
                    ELSE 'plannedFuture'
                END
            ORDER BY
                COALESCE(t.utc_start_datetime, t.start_datetime) DESC
        ) as rn
    FROM
        trips t
    LEFT JOIN
        paths p ON t.trip_id = p.trip_id
    WHERE
        t.user_id = :user_id
        AND (
            :lastLocal IS NULL
            OR t.last_modified > :lastLocal
        )
        AND (
            :public = 0
            OR t.trip_type IN ('train', 'air', 'bus', 'ferry', 'aerialway', 'tram', 'metro')
        )
)
SELECT
    *
FROM
    RankedTrips
WHERE
    rn = 1
ORDER BY
    CASE WHEN is_project = true THEN 0 ELSE 1 END,
    COALESCE(utc_start_datetime, start_datetime) DESC NULLS FIRST;