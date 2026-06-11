-- Inserts a plan-trip with its path geometry inline. :geom_ewkt is an EWKT string
-- from coords_to_ewkt (NULL for an empty path -> ST_GeomFromEWKT(NULL) = NULL).
INSERT INTO plan_trips (
    plan_id, user_id, sort_order,
    timing_mode, start_day, end_day, start_time, end_time,
    start_datetime, end_datetime, utc_start_datetime, utc_end_datetime,
    estimated_trip_duration, manual_trip_duration,
    origin_station, destination_station, trip_type, operator, line_name,
    material_type, material_type_advanced, reg, seat, notes,
    trip_length, countries, price, currency, purchase_date, waypoints, visibility, carbon,
    power_type, co2_override,
    geom, created, last_modified
)
VALUES (
    :plan_id, :user_id, :sort_order,
    :timing_mode, :start_day, :end_day, :start_time, :end_time,
    :start_datetime, :end_datetime, :utc_start_datetime, :utc_end_datetime,
    :estimated_trip_duration, :manual_trip_duration,
    :origin_station, :destination_station, :trip_type, :operator, :line_name,
    :material_type, :material_type_advanced, :reg, :seat, :notes,
    :trip_length, :countries, :price, :currency, :purchase_date, :waypoints, :visibility, :carbon,
    :power_type, :co2_override,
    ST_GeomFromEWKT(:geom_ewkt), :created, :last_modified
)
RETURNING uid
