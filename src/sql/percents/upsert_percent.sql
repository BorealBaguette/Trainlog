INSERT INTO percents (username, cc, percent)
VALUES (:username, :cc, :percent)
ON CONFLICT (username, cc) DO UPDATE SET percent = EXCLUDED.percent;
