,SplitMaterial AS (
    SELECT
        uid,
        TRIM(IFNULL(NULLIF(SUBSTR(material_type, 1, INSTR(material_type, ',') - 1), ''), material_type)) AS material_type,
        CASE
            WHEN INSTR(material_type, ',') THEN TRIM(SUBSTR(material_type, INSTR(material_type, ',') + 1))
            ELSE NULL
        END AS rest,
        past,
        plannedFuture,
        future
    FROM counted
    WHERE (:username IS NULL OR username = :username) AND future = 0

    UNION ALL

    SELECT
        uid,
        TRIM(IFNULL(NULLIF(SUBSTR(rest, 1, INSTR(rest, ',') - 1), ''), rest)),
        CASE
            WHEN INSTR(rest, ',') THEN TRIM(SUBSTR(rest, INSTR(rest, ',') + 1))
            ELSE NULL
        END,
        past,
        plannedFuture,
        future
    FROM SplitMaterial
    WHERE rest IS NOT NULL AND TRIM(rest) <> ''
)

SELECT 
    CASE 
        WHEN :tripType IN ('air', 'helicopter') AND a.iata IS NOT NULL THEN a.manufacturer || ' ' || a.model
        ELSE c.material_type
    END AS material,
    SUM(c.past) AS past,
    SUM(c.plannedFuture) AS plannedFuture,
    (SUM(c.past) + SUM(c.plannedFuture)) AS count
FROM 
    SplitMaterial c
LEFT JOIN 
    airliners a ON c.material_type = a.iata
GROUP BY 
    CASE 
        WHEN :tripType IN ('air', 'helicopter') AND a.iata IS NOT NULL THEN a.manufacturer || ' ' || a.model
        ELSE c.material_type
    END
ORDER BY 
    count DESC;
