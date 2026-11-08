-- What a share link shows besides the trips: prices and CO₂ (src/share_view.py).
--
-- NULL follows the owner's account settings (User.share_price / share_carbon),
-- which is what the usual "Create link" / copy button hands out. Otherwise the
-- choice made when the link was shared, baked into the key: 'p' prices, 'c' CO₂,
-- 'pc' both, 'n' neither. Such a selection hashes differently from the plain one
-- (ids_hash covers the flags too), so it gets a key of its own and nothing in the
-- URL can be removed to fall back to the owner's settings.
ALTER TABLE trip_selections ADD COLUMN share_flags TEXT;
