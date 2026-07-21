from src.sql import SqlTemplate

get_trip_operator_logos_query = SqlTemplate(
    "src/sql/operators/get_trip_operator_logos.sql"
)
insert_trip_operators_query = SqlTemplate("src/sql/operators/insert_trip_operators.sql")
resolve_operator_names_query = SqlTemplate(
    "src/sql/operators/resolve_operator_names.sql"
)
