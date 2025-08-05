,SplitOperators AS (
    SELECT 
        uid,
        TRIM(IFNULL(NULLIF(SUBSTR(operator, 1, INSTR(operator, ',') - 1), ''), operator)) AS operator,
        CASE 
            WHEN INSTR(operator, ',') THEN TRIM(SUBSTR(operator, INSTR(operator, ',') + 1))
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
    FROM SplitOperators
    WHERE rest IS NOT NULL AND TRIM(rest) <> ''
),

-- Step 4: Calculate the top 10
operators AS (
    SELECT 
        operator,
        SUM(past) as 'past',
        SUM(plannedFuture) as 'plannedFuture',
        (SUM(past) + SUM(plannedFuture)) as 'count'
    FROM SplitOperators
    GROUP BY operator
    ORDER BY count DESC
)


-- Step 6: Combine top_10 and 'Others' into final result
SELECT operator, past, plannedFuture, count FROM operators