from src.sql import SqlTemplate

insert_trip_query = SqlTemplate("src/sql/trips/insert_trip.sql")
duplicate_trip_query = SqlTemplate("src/sql/trips/duplicate_trip.sql")
update_trip_query = SqlTemplate("src/sql/trips/update_trip.sql")
update_trip_type_query = SqlTemplate("src/sql/trips/update_trip_type.sql")
delete_trip_query = SqlTemplate("src/sql/trips/delete_trip.sql")
update_ticket_null_query = SqlTemplate("src/sql/trips/update_ticket_null.sql")
attach_ticket_query = SqlTemplate("src/sql/trips/attach_ticket.sql")