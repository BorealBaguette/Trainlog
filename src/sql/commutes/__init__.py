from src.sql import SqlTemplate

insert_commute_query = SqlTemplate("src/sql/commutes/insert_commute.sql")
insert_commute_occurrence_query = SqlTemplate(
    "src/sql/commutes/insert_commute_occurrence.sql"
)
insert_commute_route_trip_query = SqlTemplate(
    "src/sql/commutes/insert_commute_route_trip.sql"
)
get_max_sequence_query = SqlTemplate("src/sql/commutes/get_max_sequence.sql")
get_first_occurrence_id_query = SqlTemplate(
    "src/sql/commutes/get_first_occurrence_id.sql"
)
touch_commute_query = SqlTemplate("src/sql/commutes/touch_commute.sql")
get_commute_template_query = SqlTemplate("src/sql/commutes/get_commute_template.sql")
get_commutes_query = SqlTemplate("src/sql/commutes/get_commutes.sql")
get_commutes_for_append_query = SqlTemplate(
    "src/sql/commutes/get_commutes_for_append.sql"
)
get_commute_query = SqlTemplate("src/sql/commutes/get_commute.sql")
delete_commute_query = SqlTemplate("src/sql/commutes/delete_commute.sql")
