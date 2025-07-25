UPDATE trips SET
    origin_station = :origin_station,
    destination_station = :destination_station,
    start_datetime = :start_datetime,
    end_datetime = :end_datetime,
    is_project = :is_project,
    utc_start_datetime = :utc_start_datetime,
    utc_end_datetime = :utc_end_datetime,
    estimated_trip_duration = :estimated_trip_duration,
    manual_trip_duration = :manual_trip_duration,
    trip_length = :trip_length,
    operator = :operator,
    countries = :countries,
    line_name = :line_name,
    created = :created,
    last_modified = :last_modified,
    trip_type = :trip_type,
    material_type = :material_type,
    seat = :seat,
    reg = :reg,
    waypoints = :waypoints,
    notes = :notes,
    price = :price,
    currency = :currency,
    ticket_id = :ticket_id,
    purchase_date = :purchase_date
WHERE trip_id = :trip_id
