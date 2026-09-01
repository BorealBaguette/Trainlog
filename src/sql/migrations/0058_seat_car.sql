-- Which car (0-based position in the trip's resolved trainset strip) the
-- free-text `seat` belongs to. There is no real-world car numbering to key
-- off (see `material_type_advanced`), so this points at a position in that
-- trip's own composition instead — set by clicking the car in the trainset
-- builder, read back to draw a marker on the same car when the strip is
-- displayed. NULL means "no car marked" (the default for every existing row
-- and for any trip whose seat/trainset never goes through the builder, e.g.
-- CSV/GPX import).
ALTER TABLE trips ADD COLUMN seat_car INTEGER;
