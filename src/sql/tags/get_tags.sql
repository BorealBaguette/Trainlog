SELECT
    tags.uuid,
    tags.uid,
    tags.name,
    tags.colour,
    tags.type,
    tags.username AS owner,
    (tags.username = :username) AS is_owner,
    EXISTS (
        SELECT 1 FROM tag_members tm
        WHERE tm.tag_id = tags.uid AND tm.status = 'accepted'
    ) AS is_shared,
    string_agg(tags_associations.trip_id::text, ',') AS trip_ids
FROM tags
LEFT JOIN tags_associations ON tags.uid = tags_associations.tag_id
WHERE tags.username = :username
   OR tags.uid IN (
        SELECT tag_id FROM tag_members
        WHERE username = :username AND status = 'accepted'
   )
GROUP BY tags.uid
