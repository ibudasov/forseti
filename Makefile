.DEFAULT_GOAL := help
DOCKER_COMPOSE ?= $(shell if docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo docker-compose; fi)
# Tests drop and recreate every table, so they must never point at the application database.
POSTGRES_TEST_DB ?= forseti_test
TEST_DATABASE_URL ?= postgresql://$${POSTGRES_USER:-user}:$${POSTGRES_PASSWORD:-password}@postgresql:5432/$(POSTGRES_TEST_DB)

.PHONY: check-compose help migrate migration db-shell test replay ingest ingest-earnings ingest-rag analyze up down adk-web lint typecheck check scorecard scorecard-baseline

check-compose:
	@if [ -z "$(DOCKER_COMPOSE)" ]; then \
		echo "Error: neither 'docker compose' nor 'docker-compose' is available."; \
		exit 1; \
	fi

help:
	@echo "Available targets:"
	@echo "  make migrate          # Run Alembic migrations to head"
	@echo "  make migration name=...  # Generate a new Alembic revision"
	@echo "  make db-shell         # Open psql against the Postgres service"
	@echo "  make test             # Run pytest inside the app container"
	@echo "  make replay RUN_ID=... # Replay an offline golden cassette"
	@echo "  make ingest           # Run structured data ingestion pipeline"
	@echo "  make ingest-earnings  # Run earnings ingestion"
	@echo "  make ingest-rag       # Run RAG document ingestion (use ticker=SYMBOL for single ticker)"
	@echo "  make analyze          # Analyze one ticker (use ticker=NVDA [mode=agentic|linear])"
	@echo "  make adk-web          # Open the ADK dev UI on :8010 (needs Vertex credentials)"
	@echo "  make lint             # Run flake8 checks"
	@echo "  make typecheck        # Run mypy checks"
	@echo "  make check            # Run lint and typecheck"
	@echo "  make scorecard        # Compute the product scorecard and fail on regression"
	@echo "  make scorecard-baseline  # Rewrite docs/scorecard-baseline.json (explicit, reviewable diff)"

migrate: check-compose
	$(DOCKER_COMPOSE) run --rm --build app python -m alembic upgrade head

migration: check-compose
ifeq ($(strip $(name)),)
	@echo "Error: name is required. Run 'make migration name=your_migration_name'"
	@exit 1
else
	$(DOCKER_COMPOSE) run --rm --build \
		-v "$$PWD/migrations:/app/migrations" \
		app python -m alembic revision --autogenerate -m "$(name)"
endif

db-shell: check-compose
	@$(DOCKER_COMPOSE) exec postgresql psql -U "$${POSTGRES_USER:-user}" -d "$${POSTGRES_DB:-forseti}"

test: check-compose
	@if [ -z "$(DOCKER_COMPOSE)" ]; then echo "Error: Neither 'docker compose' nor 'docker-compose' is available."; exit 1; fi
	$(DOCKER_COMPOSE) run --rm --build \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-e PIPELINE_MODE=linear \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		-v /var/run/docker.sock:/var/run/docker.sock \
		app python -m pytest tests --cov=app --cov=agents --cov-report=term-missing --cov-fail-under=70 \
			-W "ignore:SelectableGroups dict interface is deprecated. Use select.:DeprecationWarning" \
			-W "ignore:BaseAgentConfig is deprecated and will be removed in future versions.:DeprecationWarning"

replay: check-compose
	@if [ -z "$(RUN_ID)" ]; then echo "Error: RUN_ID is required (for example RUN_ID=happy-path)"; exit 1; fi
	$(DOCKER_COMPOSE) run --rm \
		-v "$$PWD/agents:/app/agents:ro" \
		-v "$$PWD/tests/fixtures/golden:/app/tests/fixtures/golden:ro" \
		app python -m agents.observability.cassette --run-id "$(RUN_ID)"

ingest: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		app python -m app.ingestion.run --source all
	make ingest-rag

ingest-earnings: check-compose
	$(DOCKER_COMPOSE) run --rm --build app python -m app.ingestion.run --source earnings

ingest-rag: check-compose
	$(if $(ticker),	$(DOCKER_COMPOSE) run --rm app python -m app.rag.cli --ticker $(ticker),$(DOCKER_COMPOSE) run --rm app python -m app.rag.cli --all-active)

analyze: check-compose
	@if [ -z "$(ticker)" ]; then \
		echo "Error: ticker is required. Run 'make analyze ticker=NVDA [mode=agentic|linear]'"; \
		exit 1; \
	fi
	@curl -s -X POST "http://127.0.0.1:8000/analyze?include_trace=true$(if $(mode),&pipeline=$(mode),)" \
		-H 'content-type: application/json' \
		-d '{"ticker":"$(ticker)"}' | python3 -m json.tool

up: check-compose
	$(DOCKER_COMPOSE) up --force-recreate app

adk-web: check-compose
	$(DOCKER_COMPOSE) run --rm --build -p 8010:8010 app python -m google.adk.cli web agents --host 0.0.0.0 --port 8010

down: check-compose
	$(DOCKER_COMPOSE) down

lint: check-compose
	$(DOCKER_COMPOSE) run --rm --build -v "$$PWD/tests:/app/tests" app python -m flake8 app agents tests --count --select=E9,F63,F7,F82 --show-source --statistics
	$(DOCKER_COMPOSE) run --rm -v "$$PWD/tests:/app/tests" app python -m flake8 app agents tests --count --max-complexity=10 --max-line-length=127 --statistics

typecheck: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		-v "$$PWD/pyproject.toml:/app/pyproject.toml:ro" \
		app python -m mypy --explicit-package-bases --follow-imports=skip app agents

check: lint typecheck

scorecard: check-compose
	@# Deliberately no --build: `make test`/`make typecheck` already build the
	@# image earlier in the pipeline, and rebuilding here would reprint Docker's
	@# build log into the scorecard output that gets posted as a PR comment.
	@# `docker compose run` builds the image automatically if it is missing.
	$(DOCKER_COMPOSE) run --rm \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/docs:/app/docs" \
		app python -m scripts.scorecard \
			--fixture tests/fixtures/scorecard/universe.json \
			--markdown \
			--baseline docs/scorecard-baseline.json \
			--fail-on-regression

scorecard-baseline: check-compose
	$(DOCKER_COMPOSE) run --rm \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/docs:/app/docs" \
		app python -m scripts.scorecard \
			--fixture tests/fixtures/scorecard/universe.json \
			--json > docs/scorecard-baseline.json