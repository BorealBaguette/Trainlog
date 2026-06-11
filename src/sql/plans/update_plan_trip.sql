-- Edit a plan-trip's timing + light metadata (keeps the existing path/geom).
UPDATE plan_trips
SET timing_mode = :timing_mode,
    start_day = :start_day,
    end_day = :end_day,
    start_time = :start_time,
    end_time = :end_time,
    start_datetime = :start_datetime,
    end_datetime = :end_datetime,
    utc_start_datetime = :utc_start_datetime,
    utc_end_datetime = :utc_end_datetime,
    manual_trip_duration = :manual_trip_duration,
    operator = :operator,
    line_name = :line_name,
    notes = :notes,
    visibility = :visibility,
    last_modified = :last_modified
WHERE uid = :uid AND user_id = :user_id
