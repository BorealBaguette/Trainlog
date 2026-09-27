-- Resolve station_labels against the registry. `scoped` limits it to the labels of the
-- given stations.
--
-- Never write a bind-parameter reference in these comments: SQLAlchemy parses them too.
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
