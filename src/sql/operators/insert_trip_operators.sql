-- Resolve `trips.operator` free text into trip_operators rows.
--
-- Used both to refresh single trips (sync_trip_operators) and to rebuild the whole
-- table (rebuild_trip_operators); the migration 0043 backfill is a frozen copy of
-- this statement. Callers delete the affected rows first.
--
-- The trip's operator pool (operator_type_bucket) is a *preference*, not a filter:
-- an alias in the trip's own pool wins, but a match in another pool is still used
-- when the trip's pool has none. That keeps "Avis" on a car trip pointing at the
-- car-rental company while a car trip logged as "Bolt" — which only exists as a
-- transport operator — still finds its logo, as it did before aliases existed.
--
-- DISTINCT ON collapses names resolving to the same operator within one trip, so a
-- trip logged as 'SBB, CFF' counts once for SBB rather than twice once those two
-- spellings are aliased together. Unresolved names dedupe on their normalised form.
{% if not as_select %}
INSERT INTO trip_operators (trip_id, position, user_id, raw_name, operator_id)
{% endif %}
SELECT trip_id,
       -- Named because this same SELECT is materialised as a table by the
       -- consistency check; the INSERT above matches columns positionally.
       (row_number() OVER (PARTITION BY trip_id ORDER BY ord) - 1)::smallint AS position,
       user_id,
       raw_name,
       operator_id
FROM (
    SELECT DISTINCT ON (t.trip_id, COALESCE(a.operator_id::text, operator_normalize(x.raw)))
           t.trip_id,
           x.ord,
           t.user_id,
           TRIM(x.raw) AS raw_name,
           a.operator_id
    FROM trips t
    CROSS JOIN LATERAL unnest(string_to_array(t.operator, ',')) WITH ORDINALITY AS x(raw, ord)
    LEFT JOIN LATERAL (
        SELECT al.operator_id
        FROM operator_aliases al
        WHERE al.normalized = operator_normalize(x.raw)
        ORDER BY (al.operator_type = operator_type_bucket(t.trip_type)) DESC, al.operator_id
        LIMIT 1
    ) a ON TRUE
    WHERE operator_normalize(x.raw) IS NOT NULL
{% if scoped %}
      AND t.trip_id = ANY(:trip_ids)
{% endif %}
    ORDER BY t.trip_id,
             COALESCE(a.operator_id::text, operator_normalize(x.raw)),
             x.ord
) deduped;
