# Most useful actions in this file:
#
# * make start: starts all the docker containers
# * make start-db: starts the db container only, locally. Useful for running flask
#   locally, outside of docker
# * make start-local: starts the app locally, outside of docker
# * make psql: starts a psql prompt, connected to the db container


# read the .env file to set up env vars
include .env

# start the app, dockerized
start:
	docker compose up --build -d

# start the database only
start-db:
	docker compose up trainlog_db -d

# start the app only, locally (non-docker)
start-local: start-db
	env $(cat .env | xargs) POSTGRES_HOST=localhost gunicorn --timeout 1000 --bind 0.0.0.0:5000 app:app --reload

# run trip sync + comparison
sync-trips:
	env $(cat .env | xargs) POSTGRES_HOST=localhost python -c "from src.db_sync import sync_db_from_sqlite; sync_db_from_sqlite()"

# stop all containers
stop:
	docker compose down

# show application logs
logs:
	docker compose logs -f --tail 1000

# start psql connected to the app's db
psql:
	docker compose exec trainlog_db psql -U ${POSTGRES_USER} ${POSTGRES_DB}

# refresh schema.sql
generate-schema-sql:
	@echo "-- File generated automatically, do not modify by hand!" > src/sql/migrations/schema.sql
	@echo "-- Use \`make generate-schema-sql\` instead" >> src/sql/migrations/schema.sql
	docker compose exec trainlog_db pg_dump -U ${POSTGRES_USER} ${POSTGRES_DB} --schema-only >> src/sql/migrations/schema.sql
	@echo "SET search_path TO DEFAULT;" >> src/sql/migrations/schema.sql

.PHONY: start start-db start-app stop psql generate-schema-sql
