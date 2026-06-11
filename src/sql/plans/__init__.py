from src.sql import SqlTemplate

insert_plan_query = SqlTemplate("src/sql/plans/insert_plan.sql")
get_plan_query = SqlTemplate("src/sql/plans/get_plan.sql")
get_user_plans_query = SqlTemplate("src/sql/plans/get_user_plans.sql")
update_plan_query = SqlTemplate("src/sql/plans/update_plan.sql")
archive_plan_query = SqlTemplate("src/sql/plans/archive_plan.sql")
delete_plan_query = SqlTemplate("src/sql/plans/delete_plan.sql")
insert_plan_trip_query = SqlTemplate("src/sql/plans/insert_plan_trip.sql")
get_plan_trips_query = SqlTemplate("src/sql/plans/get_plan_trips.sql")
update_plan_trip_query = SqlTemplate("src/sql/plans/update_plan_trip.sql")
update_plan_trip_full_query = SqlTemplate("src/sql/plans/update_plan_trip_full.sql")
delete_plan_trip_query = SqlTemplate("src/sql/plans/delete_plan_trip.sql")
reorder_plan_trip_query = SqlTemplate("src/sql/plans/reorder_plan_trip.sql")
