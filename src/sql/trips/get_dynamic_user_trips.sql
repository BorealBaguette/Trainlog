-- PostgreSQL. Builds the FilteredTrips CTE consumed by the dynamic-trips
-- endpoint, which appends "SELECT COUNT(*)/SELECT * FROM FilteredTrips [WHERE ...]
-- [ORDER BY ...] [LIMIT/OFFSET]". Columns are aliased to the legacy SQLite `trip`
-- names (uid, type, purchasing_date) so the appended filters/sorts keep working;
-- rows are adapted back to the legacy shape in Python (adapt_pg_trip_row).
WITH base AS (
    SELECT
        trip_id AS uid,
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
        created,
        last_modified,
        trip_type AS type,
        material_type,
        material_type_advanced,
        seat,
        reg,
        waypoints,
        notes,
        price,
        currency,
        ticket_id,
        purchase_date AS purchasing_date,
        carbon,
        visibility,
        departure_delay,
        arrival_delay,
        power_type,
        COALESCE(utc_start_datetime, start_datetime) AS utc_filtered_start_datetime,
        COALESCE(utc_end_datetime, end_datetime) AS utc_filtered_end_datetime
    FROM trips
    WHERE user_id = :user_id
),
sub AS (
    SELECT
        base.*,
        CASE
            WHEN base.utc_filtered_start_datetime IS NOT NULL
                 AND base.utc_filtered_end_datetime IS NOT NULL
                 AND base.utc_filtered_start_datetime <> base.utc_filtered_end_datetime
            THEN EXTRACT(EPOCH FROM (base.utc_filtered_end_datetime - base.utc_filtered_start_datetime))
            ELSE COALESCE(base.manual_trip_duration, base.estimated_trip_duration)
        END AS trip_duration_seconds,
        o.short_name AS operator_name,
        base.start_datetime::time AS start_time,
        base.end_datetime::time AS end_time,
        (SELECT l.logo_url
         FROM operator_logos l
         WHERE l.operator_id = o.operator_id
           AND (l.effective_date <= base.utc_filtered_start_datetime
                OR l.effective_date IS NULL
                OR base.utc_filtered_start_datetime IS NULL)
         ORDER BY l.effective_date DESC NULLS LAST, l.uid DESC
         LIMIT 1) AS logo_url,
        -- Every operator of a multi-operator trip, so the list can show the whole
        -- set rather than just the first. Each entry is {name, logo}; name is the
        -- spelling as typed and logo may be null (rendered as a text pill).
        --
        -- Guarded by the comma test on purpose. This lookup is keyed by trip, so
        -- unlike the single-operator join above it cannot be memoized — every row
        -- would cost an index descent. Only ~1.6% of trips have more than one
        -- operator, so the CASE keeps that cost off the other 98.4%.
        --
        -- Not capped: the compact list cell shows at most the first four (a
        -- fixed-height 2x2 grid), but the responsive detail row lists every one, and
        -- on mobile that detail row is the only place a name is readable at all.
        -- Trips with more than four operators are rare (256 with four, 15 with more,
        -- out of 651k), so the extra rows cost effectively nothing.
        CASE WHEN POSITION(',' IN base.operator) > 0 THEN (
            SELECT json_agg(json_build_object('name', e.raw_name, 'logo', e.logo_url)
                            ORDER BY e.position)
            FROM (
                SELECT tv.position,
                       tv.raw_name,
                       (SELECT l.logo_url
                        FROM operator_logos l
                        WHERE l.operator_id = tv.operator_id
                          AND (l.effective_date <= base.utc_filtered_start_datetime
                               OR l.effective_date IS NULL
                               OR base.utc_filtered_start_datetime IS NULL)
                        ORDER BY l.effective_date DESC NULLS LAST, l.uid DESC
                        LIMIT 1) AS logo_url
                FROM trip_operators tv
                WHERE tv.user_id = base.user_id AND tv.trip_id = base.uid
                ORDER BY tv.position
            ) e
        ) END AS operator_logos
    FROM base
    -- The trip's first operator, resolved through operator_aliases. Replaces a match
    -- of split_part(operator, ',', 1) against operators.short_name, which found only
    -- exactly-spelled names and needed an ORDER BY ... LIMIT 1 guard because
    -- short_name was not unique. Resolution is now case- and accent-insensitive, so a
    -- trip logged 'cff' or 'S.B.B.' picks up the SBB logo. `operator` (the raw text)
    -- is what the row displays — the alias only decides which logo to show.
    --
    -- Deliberately keyed on the operator *name*, not on the trip: a user's trips
    -- repeat a few hundred operator names across thousands of rows (13k trips / 798
    -- distinct names is typical), so Postgres memoizes this lateral and hits the cache
    -- ~95% of the time. Looking the same thing up via trip_operators keyed on trip_id
    -- defeats that cache — every row is a unique key, so it becomes one index descent
    -- per row and measurably slower. trip_operators is the right structure for
    -- aggregating (see the stats CTE); name lookup is the right one here.
    LEFT JOIN LATERAL (
        SELECT a.operator_id, op.short_name
        FROM operator_aliases a
        JOIN operators op ON op.operator_id = a.operator_id
        WHERE a.normalized = operator_normalize(split_part(base.operator, ',', 1))
        -- Prefer the trip's own pool, but fall back to any pool, exactly as
        -- insert_trip_operators.sql does.
        ORDER BY (a.operator_type = operator_type_bucket(base.type)) DESC, a.operator_id
        LIMIT 1
    ) o ON TRUE
),
trip_tags AS (
    -- Scope to the current user's trips: without this the aggregate runs over the
    -- entire tags_associations table (all users) on every request, which dominated
    -- the query cost. base is already filtered to :user_id.
    SELECT ta.trip_id,
           json_agg(json_build_object('tag_id', ta.tag_id, 'name', t.name)) AS tags
    FROM tags_associations ta
    JOIN tags t ON ta.tag_id = t.uid
    WHERE ta.trip_id IN (SELECT uid FROM base)
    GROUP BY ta.trip_id
),
-- NOTE: the global free-text search predicate is NOT baked into FilteredTrips. It
-- is appended to the outer "SELECT ... FROM FilteredTrips WHERE ..." by the Python
-- builder (get_trips_api_internal) only when the search box is non-empty. With an
-- empty search the predicate is absent, so Postgres can elide the airliners join
-- (its columns are unused by the COUNT query) and there is no tickets join at all,
-- roughly halving the count-query cost. The ticket-name match is done there as a
-- correlated EXISTS on ticket_id, so this CTE no longer needs to join tickets.
-- (Keep this CTE last so the file ends with ')': the builder concatenates the final
-- SELECT directly onto this template.)
FilteredTrips AS (
    SELECT
        sub.*,
        airliners.iata,
        airliners.manufacturer,
        airliners.model,
        sub.trip_length / NULLIF(sub.trip_duration_seconds, 0) AS trip_speed,
        CASE
            WHEN NOT sub.is_project
                 AND (sub.utc_filtered_start_datetime IS NULL OR NOW() > sub.utc_filtered_start_datetime)
            THEN 1 ELSE 0
        END AS past,
        CASE
            WHEN sub.utc_filtered_start_datetime IS NOT NULL AND NOW() <= sub.utc_filtered_start_datetime
            THEN 1 ELSE 0
        END AS "plannedFuture",
        CASE
            WHEN sub.utc_filtered_start_datetime IS NULL AND sub.is_project
            THEN 1 ELSE 0
        END AS future,
        trip_tags.tags AS tags
    FROM sub
    LEFT JOIN airliners ON sub.material_type = airliners.iata
    LEFT JOIN trip_tags ON sub.uid = trip_tags.trip_id
    WHERE
        (CASE
            WHEN NOT sub.is_project
                 AND (sub.utc_filtered_start_datetime IS NULL OR NOW() > sub.utc_filtered_start_datetime)
            THEN 1 ELSE 0
        END) = :past
        -- Past page's "show upcoming" toggle: also include dated future trips
        -- (plannedFuture). With the default temporal desc sort they land on top.
        OR (
            :include_planned = 1
            AND sub.utc_filtered_start_datetime IS NOT NULL
            AND NOW() <= sub.utc_filtered_start_datetime
        )
)
