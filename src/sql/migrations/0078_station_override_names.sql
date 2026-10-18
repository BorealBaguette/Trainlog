-- A quai station's name in given languages, where OSM has none Trainlog wants ({"ja":
-- "ホワイロウ"}): what OSM would not take (a transliteration, Trainlog's own spelling), or a
-- name until OSM gains it. Shown to readers of that language over quai's (src/quai.py).
ALTER TABLE station_overrides ADD COLUMN IF NOT EXISTS names JSONB;
