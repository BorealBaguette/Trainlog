-- World-square percentages are small fractional values (typically 0-3%) computed
-- with round(land_percentage, 2). The percents.percent column was INTEGER, so
-- those values were rounded to whole numbers on insert, collapsing the
-- leaderboard to 0/1/2/3. Widen the column to keep two decimals.
-- (train_countries still stores integer percents via math.ceil, unaffected.)
ALTER TABLE percents ALTER COLUMN percent TYPE DOUBLE PRECISION;
