-- Optional manual scale calibration: how many image pixels represent one real-world
-- metre for this wagon's artwork. When set, the renderer converts pixel height to real
-- height (naturalHeight / px_per_meter) and draws every calibrated wagon at one shared
-- on-screen metres-per-pixel scale, overriding the per-strip median heuristic. NULL =
-- uncalibrated (fall back to median-relative sizing).
ALTER TABLE wagons ADD COLUMN IF NOT EXISTS px_per_meter REAL;
