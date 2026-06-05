SELECT username, cc, percent
FROM percents
WHERE percent > 0
AND cc {{ equals }} 'world_squares'
AND username = ANY(:usernames)
ORDER BY cc, percent DESC
