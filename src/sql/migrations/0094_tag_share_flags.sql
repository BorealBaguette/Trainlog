-- Share keys for a tag: one tag can be sent with different choices of what the link
-- shows (src/share_view.py), each its own key.
--
-- With tag_uuid set the key resolves to the tag's current trips, so a link keeps
-- showing trips added later, and trip_ids stays empty.
ALTER TABLE trip_selections ADD COLUMN tag_uuid TEXT;
