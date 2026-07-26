INSERT INTO trips (
    user_id,
    origin_station,
    destination_station,
    start_datetime,
    end_datetime,
    is_project,
    utc_start_datetime,
    utc_end_datetime,
    estimated_trip_duration,
    manual_trip_duration,
    trip_length,
    operator,
    countries,
    line_name,
    trip_type,
    material_type,
    material_type_advanced,
    reg,
    waypoints
)
SELECT
    :new_user_id,
    origin_station,
    destination_station,
    start_datetime,
    end_datetime,
    is_project,
    utc_start_datetime,
    utc_end_datetime,
    estimated_trip_duration,
    manual_trip_duration,
    trip_length,
    operator,
    countries,
    line_name,
    trip_type,
    material_type,
    material_type_advanced,
    reg,
    waypoints
FROM trips
WHERE trip_id = :trip_id
RETURNING trip_id
