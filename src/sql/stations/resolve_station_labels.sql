-- Resolve station_labels against the registry: fill in station_id for every spelling.
--
-- This is what register_labels() and rebuild_labels() run (src/stations.py). `scoped` limits
-- it to the labels naming one station, so an alias change costs a handful of rows rather
-- than a full pass.
--
-- Do not write a bare bind-parameter reference in these comments: SQLAlchemy's parameter
-- parser does not skip SQL comments, and an unscoped run would then demand a value for it.
--
-- The matching rule, including how ambiguity is handled, lives in station_resolve_alias()
-- (migration 0060) so that this query, resolve_station_names.sql and delete_station() cannot
-- drift apart.
UPDATE station_labels sl
SET station_id = m.station_id
FROM (
    SELECT l.label_id,
           station_resolve_alias(
               l.normalized,
               l.station_type,
               station_flag_country(l.sample_label)
           ) AS station_id
    FROM station_labels l
    {% if scoped %}
    WHERE l.station_id = ANY(:station_ids)
       OR l.normalized IN (
            SELECT normalized FROM station_aliases WHERE station_id = ANY(:station_ids)
       )
    {% endif %}
) m
WHERE sl.label_id = m.label_id
  AND sl.station_id IS DISTINCT FROM m.station_id;
