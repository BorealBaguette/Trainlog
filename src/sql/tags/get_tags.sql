SELECT
    tags.uuid,
    tags.uid,
    tags.name,
    tags.colour,
    string_agg(tags_associations.trip_id::text, ',') AS trip_ids
FROM tags
LEFT JOIN tags_associations ON tags.uid = tags_associations.tag_id
WHERE tags.username = :username
GROUP BY tags.uid
