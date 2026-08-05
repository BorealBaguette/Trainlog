-- Recursively split comma-separated material types
WITH RECURSIVE material_split AS (
    SELECT
        trip_id,
        CASE
            WHEN material_type IS NULL OR TRIM(material_type) = '' THEN NULL
            WHEN POSITION(',' IN material_type) > 0 THEN TRIM(SUBSTRING(material_type, 1, POSITION(',' IN material_type) - 1))
            ELSE TRIM(material_type)
        END AS material_type,
        CASE
            WHEN material_type IS NULL OR TRIM(material_type) = '' THEN NULL
            WHEN POSITION(',' IN material_type) > 0 THEN TRIM(SUBSTRING(material_type FROM POSITION(',' IN material_type) + 1))
            ELSE NULL
        END AS rest,
        trip_length, is_past, is_planned_future, is_project, trip_duration, carbon, arrival_delay
    FROM time_categories
    WHERE is_project IS FALSE

    UNION ALL

    SELECT
        trip_id,
        CASE
            WHEN rest IS NULL OR TRIM(rest) = '' THEN NULL
            WHEN POSITION(',' IN rest) > 0 THEN TRIM(SUBSTRING(rest, 1, POSITION(',' IN rest) - 1))
            ELSE TRIM(rest)
        END,
        CASE
            WHEN rest IS NULL OR TRIM(rest) = '' THEN NULL
            WHEN POSITION(',' IN rest) > 0 THEN TRIM(SUBSTRING(rest FROM POSITION(',' IN rest) + 1))
            ELSE NULL
        END,
        trip_length, is_past, is_planned_future,  is_project, trip_duration, carbon, arrival_delay
    FROM material_split
    WHERE rest IS NOT NULL AND TRIM(rest) != ''
)
SELECT * FROM material_split
WHERE material_type IS NOT NULL