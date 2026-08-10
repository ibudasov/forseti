DEFAULT_GOAL := help
DOCKER_COMPOSE := $(shell if command -v docker-compose >/dev/null 2>&1; then echo docker-compose; elif docker compose version >/dev/null 2>&1; then echo "docker compose"; else echo docker-compose; fi)

.PHONY: help migrate migration db-shell test ingest ingest-rag

help:
	@echo "Available targets:"
	@echo "  make migrate          # Run Alembic migrations to head"
	@echo "  make migration name=...  # Generate a new Alembic revision"
	@echo "  make db-shell         # Open psql against the Postgres service"
	@echo "  make test             # Run pytest inside the app container"
	@echo "  make ingest           # Run structured data ingestion pipeline"
	@echo "  make ingest-rag       # Run RAG document ingestion (use ticker=SYMBOL for single ticker)"

migrate:
	$(DOCKER_COMPOSE) run --rm --build app python -m alembic upgrade head

migration:
	ifeq (,$(name))
		$(error name is required. Run `make migration name=your_migration_name`)
	endif
	$(DOCKER_COMPOSE) run --rm --build app python -m alembic revision --autogenerate -m "$(name)"

db-shell:
	@$(DOCKER_COMPOSE) exec postgresql psql -U "$${POSTGRES_USER:-user}" -d "$${POSTGRES_DB:-forseti}"

test:
	$(DOCKER_COMPOSE) run --rm --build \
		-e TEST_DATABASE_URL=postgresql://$${POSTGRES_USER:-user}:$${POSTGRES_PASSWORD:-password}@postgresql:5432/$${POSTGRES_DB:-forseti} \
		-v $(PWD)/tests:/app/tests \
		-v /var/run/docker.sock:/var/run/docker.sock \
		app python -m pytest tests \
			-W "ignore:SelectableGroups dict interface is deprecated. Use select.:DeprecationWarning" \
			-W "ignore:BaseAgentConfig is deprecated and will be removed in future versions.:DeprecationWarning"

ingest:
	$(DOCKER_COMPOSE) run --rm \
		app python -m app.ingestion.run --source all
	make ingest-rag

ingest-rag:
	$(if $(ticker),$(DOCKER_COMPOSE) run --rm app python -m app.rag.cli --ticker $(ticker),$(DOCKER_COMPOSE) run --rm app python -m app.rag.cli --all-active)

up:
	$(DOCKER_COMPOSE) up

down:
	$(DOCKER_COMPOSE) down