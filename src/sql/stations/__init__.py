from src.sql import SqlTemplate

get_airports_query = SqlTemplate("src/sql/stations/get_airports.sql")
get_train_stations_query = SqlTemplate("src/sql/stations/get_train_stations.sql")
get_manual_stations_query = SqlTemplate("src/sql/stations/get_manual_stations.sql")
