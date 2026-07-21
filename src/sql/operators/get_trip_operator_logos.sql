-- Every resolved operator of one trip, in the order the names were typed, each with
-- the logo appropriate to the trip's date.
--
-- Replaces a Python loop that ran two queries per operator per trip (one to look the
-- operator up by exact short_name, one for its logo).
--
-- :mode picks the era rule, matching get_trip.sql:
--   'oldest'  — trip in the past with an unknown date: earliest logo
--   'latest'  — project/future trip: most recent logo
--   'at_date' — real date: newest logo not effective after the trip start
SELECT tv.position,
       tv.raw_name,
       tv.operator_id,
       op.short_name,
       l.logo_url
FROM trip_operators tv
JOIN operators op ON op.operator_id = tv.operator_id
LEFT JOIN LATERAL (
    SELECT l.logo_url
    FROM operator_logos l
    WHERE l.operator_id = tv.operator_id
      AND (
          :mode <> 'at_date'
          OR l.effective_date <= CAST(:start AS timestamp)
          OR l.effective_date IS NULL
      )
    ORDER BY
        -- Only one of these two keys is active for a given :mode; the other is a
        -- constant NULL and so does not affect the ordering.
        CASE WHEN :mode = 'oldest' THEN l.effective_date END ASC NULLS FIRST,
        CASE WHEN :mode <> 'oldest' THEN l.effective_date END DESC NULLS LAST,
        l.uid DESC
    LIMIT 1
) l ON TRUE
WHERE tv.trip_id = :trip_id
ORDER BY tv.position;
