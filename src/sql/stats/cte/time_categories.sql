SELECT *,
CASE
  WHEN is_project = false
    AND (filtered_datetime IS NULL OR NOW() > filtered_datetime)
  THEN 1 ELSE 0
END AS is_past,  -- both definite and indefinite past

CASE
  WHEN is_project = false
    AND filtered_datetime IS NOT NULL
    AND NOW() <= filtered_datetime
  THEN 1 ELSE 0
END AS is_planned_future,  -- definite future

-- trip_duration calculation
CASE
  WHEN :user_id IS NULL THEN
    CASE
      WHEN COALESCE(
        EXTRACT(EPOCH FROM (utc_end_datetime - utc_start_datetime)) - COALESCE(departure_delay, 0) + COALESCE(arrival_delay, 0),
        manual_trip_duration,
        estimated_trip_duration,
        0
      ) BETWEEN 0 AND (10 * 24 * 60 * 60)
      THEN COALESCE(
        EXTRACT(EPOCH FROM (utc_end_datetime - utc_start_datetime)) - COALESCE(departure_delay, 0) + COALESCE(arrival_delay, 0),
        manual_trip_duration,
        estimated_trip_duration,
        0
      )
      ELSE 0
    END
  ELSE
    COALESCE(
      EXTRACT(EPOCH FROM (utc_end_datetime - utc_start_datetime)) - COALESCE(departure_delay, 0) + COALESCE(arrival_delay, 0),
      manual_trip_duration,
      estimated_trip_duration,
      0
    )
END AS trip_duration
FROM base_filter