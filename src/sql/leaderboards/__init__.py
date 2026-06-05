from src.sql import SqlTemplate

# Leaderboard queries
leaderboard_stats = SqlTemplate("src/sql/leaderboards/leaderboard_stats.sql")
countries_leaderboard = SqlTemplate("src/sql/leaderboards/countries_leaderboard.sql")
carbon_leaderboard = SqlTemplate("src/sql/leaderboards/carbon_leaderboard.sql")
get_leaderboard_countries_query = SqlTemplate("src/sql/leaderboards/get_leaderboard_countries.sql")
