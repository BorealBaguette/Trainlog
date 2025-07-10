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
	env $(cat .env | xargs) POSTGRES_HOST=localhost FLASK_APP=app flask --debug run

# stop all containers
stop:
	docker compose down

# start psql connected to the app's db
psql:
	docker compose exec trainlog_db psql -U ${POSTGRES_USER} ${POSTGRES_DB}

# refresh schema.sql
make generate-schema-sql:
	@echo "-- File generated automatically, do not modify by hand!" > src/sql/migrations/schema.sql
	@echo "-- Use \`make generate-schema-sql\` instead" >> src/sql/migrations/schema.sql
	docker compose exec trainlog_db pg_dump -U ${POSTGRES_USER} ${POSTGRES_DB} --schema-only >> src/sql/migrations/schema.sql
	@echo "SET search_path TO DEFAULT;" >> src/sql/migrations/schema.sql

.PHONY: start start-db start-app stop psql generate-schema-sql
