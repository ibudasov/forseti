DEFAULT_GOAL := help

.PHONY: help migrate migration db-shell test ingest

help:
	@echo "Available targets:"
	@echo "  make migrate          # Run Alembic migrations to head"
	@echo "  make migration name=...  # Generate a new Alembic revision"
	@echo "  make db-shell         # Open psql against the Postgres service"
	@echo "  make test             # Run pytest inside the app container"
	@echo "  make ingest           # Run structured data ingestion pipeline"

migrate:
	docker-compose run --rm --build app python -m alembic upgrade head

migration:
	ifeq (,$(name))
		$(error name is required. Run `make migration name=your_migration_name`)
	endif
	docker-compose run --rm --build app python -m alembic revision --autogenerate -m "$(name)"

db-shell:
	@docker-compose exec postgresql psql -U "$${POSTGRES_USER:-user}" -d "$${POSTGRES_DB:-forseti}"

test:
	docker-compose run --rm --build \
		-e TEST_DATABASE_URL=postgresql://$${POSTGRES_USER:-user}:$${POSTGRES_PASSWORD:-password}@postgresql:5432/$${POSTGRES_DB:-forseti} \
		-v $(PWD)/tests:/app/tests \
		-v /var/run/docker.sock:/var/run/docker.sock \
		app python -m pytest tests

ingest:
	python -m app.ingestion.run --source all

up:
	docker-compose up

down:
	docker-compose down