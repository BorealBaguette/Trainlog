from src.sql import SqlTemplate

insert_trip_query = SqlTemplate("src/sql/trips/insert_trip.sql")
duplicate_trip_query = SqlTemplate("src/sql/trips/duplicate_trip.sql")
update_trip_query = SqlTemplate("src/sql/trips/update_trip.sql")
delete_trip_query = SqlTemplate("src/sql/trips/delete_trip.sql")
