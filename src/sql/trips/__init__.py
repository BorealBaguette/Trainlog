from src.sql import SqlTemplate

attach_ticket_query = SqlTemplate("src/sql/trips/attach_ticket.sql")
change_visibility_query = SqlTemplate("src/sql/trips/change_visibility.sql")
delete_trip_query = SqlTemplate("src/sql/trips/delete_trip.sql")
duplicate_trip_query = SqlTemplate("src/sql/trips/duplicate_trip.sql")
get_current_trip_query = SqlTemplate("src/sql/trips/get_current_trip.sql")
insert_trip_query = SqlTemplate("src/sql/trips/insert_trip.sql")
update_ticket_null_query = SqlTemplate("src/sql/trips/update_ticket_null.sql")
update_trip_query = SqlTemplate("src/sql/trips/update_trip.sql")
update_trip_type_query = SqlTemplate("src/sql/trips/update_trip_type.sql")
