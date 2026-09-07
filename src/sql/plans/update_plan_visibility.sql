-- Who can open the plan's share link: 'public' | 'friends' | 'private' (mirrors the
-- per-trip visibility levels). Checked by _plan_public_or_403 in app.py.
UPDATE plans
SET visibility = :visibility,
    last_modified = :last_modified
WHERE uid = :uid AND user_id = :user_id
