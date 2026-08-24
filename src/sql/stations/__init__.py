from src.sql import SqlTemplate

get_airports_query = SqlTemplate("src/sql/stations/get_airports.sql")
get_manual_stations_query = SqlTemplate("src/sql/stations/get_manual_stations.sql")
label_location_query = SqlTemplate("src/sql/stations/label_location.sql")
resolve_station_labels_query = SqlTemplate(
    "src/sql/stations/resolve_station_labels.sql"
)
search_stations_query = SqlTemplate("src/sql/stations/search_stations.sql")
