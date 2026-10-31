.DEFAULT_GOAL := help
HOST_USER := $(shell id -u):$(shell id -g)
DOCKER_COMPOSE ?= $(shell if docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo docker-compose; fi)
# Tests drop and recreate every table, so they must never point at the application database.
POSTGRES_TEST_DB ?= forseti_test
TEST_DATABASE_URL ?= postgresql+psycopg2://$${POSTGRES_USER:-user}:$${POSTGRES_PASSWORD:-password}@postgresql:5432/$(POSTGRES_TEST_DB)

.PHONY: lock check-compose reports-dir preflight-dir help migrate migration db-shell test-runtime test test-live-preflight replay ingest ingest-earnings ingest-rag rag-coverage analyze fundamental-context technical-context critique-context assess-fundamentals assess-technicals review-draft synthesize-draft eval-synthesizer eval-synthesizer-live eval-fundamental-agent eval-fundamental-agent-live fundamental-shadow-report eval-critic eval-critic-live critic-shadow-report critic-shadow-batch eval-technical-agent eval-technical-agent-live technical-shadow-report technical-backtest technical-shadow-batch trust-e2e trust-consistency live-preflight gcloud-login cloud-logout up down adk-web lint typecheck check scorecard scorecard-baseline cloudsql-proxy cloud-db-shell

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
	@echo "  make replay RUN_ID=... # Replay a recorded run offline"
	@echo "  make ingest           # Run structured data ingestion pipeline"
	@echo "  make ingest-earnings  # Run earnings ingestion"
	@echo "  make ingest-rag       # Run RAG document ingestion (use ticker=SYMBOL for single ticker)"
	@echo "  make rag-coverage     # Show RAG coverage (use ticker=NVDA [live=1])"
	@echo "  make analyze          # Analyze one ticker (use ticker=NVDA [mode=agentic|linear])"
	@echo "  make fundamental-context # Build one immutable fundamental-agent context (use ticker=NVDA [as_of=2026-09-08])"
	@echo "  make technical-context # Build one immutable technical-agent context (use ticker=NVDA [as_of=2026-09-08])"
	@echo "  make critique-context # Build one immutable critique context (use ticker=NVDA [as_of=2026-09-08])"
	@echo "  make assess-fundamentals # Run one fundamental analyst assessment (use ticker=NVDA [as_of=2026-09-08])"
	@echo "  make assess-technicals # Run one technical analyst assessment (use ticker=NVDA [as_of=2026-09-08])"
	@echo "  make review-draft      # Run one critic draft review (use ticker=NVDA [as_of=2026-09-08] [live=1])"
	@echo "  make synthesize-draft  # Synthesize one ticker draft (use ticker=NVDA [as_of=2026-09-08])"
	@echo "  make eval-synthesizer  # Run the offline decision-synthesizer evaluation suite"
	@echo "  make eval-synthesizer-live CONFIRM_COST=1 # Run live evaluation against Gemini"
	@echo "  make eval-fundamental-agent # Run the frozen offline fundamental-agent evaluation suite"
	@echo "  make eval-fundamental-agent-live CONFIRM_COST=yes # Run live model evaluation over the frozen suite"
	@echo "  make fundamental-shadow-report # Aggregate persisted shadow-mode runs"
	@echo "  make eval-critic      # Run the frozen offline critic evaluation suite"
	@echo "  make eval-critic-live CONFIRM_COST=yes # Run the manual live critic evaluation gate"
	@echo "  make critic-shadow-report [SAMPLE=20 OUT=reports/trust/critic-shadow-objections.csv] # Aggregate persisted critic shadow-mode runs"
	@echo "  make eval-technical-agent # Run the frozen offline technical-agent evaluation suite"
	@echo "  make eval-technical-agent-live CONFIRM_COST=yes # Run the manual live technical-agent evaluation gate"
	@echo "  make technical-shadow-report [SAMPLE=20 OUT=reports/trust/technical-shadow-sample.csv] # Aggregate persisted technical shadow-mode runs"
	@echo "  make technical-backtest [HORIZON_DAYS=20] # Offline usefulness backtest: replays persisted technical shadow effects against stored bars"
	@echo "  make technical-shadow-batch CONFIRM_COST=yes [REPEAT=1 FUNDAMENTAL_AGENT_MODE=off CRITIC_MODE=off] # Run the trust ticker matrix with TECHNICAL_AGENT_MODE=shadow"
	@echo "  make critic-shadow-batch CONFIRM_COST=yes [REPEAT=1] # Run the trust ticker matrix with CRITIC_MODE=shadow"
	@echo "  make trust-e2e CONFIRM_COST=yes # Run the manual live agentic-vs-linear trust matrix"
	@echo "  make trust-consistency CONFIRM_COST=yes [REPEAT=5] # Run repeated live trust matrix consistency checks"
	@echo "  make live-preflight CONFIRM_COST=yes [TICKERS=NVDA,AMD] # Run a live linear/agentic pre-flight"
	@echo "  make gcloud-login    # Sign in to gcloud and create application-default credentials"
	@echo "  make cloud-logout    # Revoke Google and stored GitHub credentials"
	@echo "  make gh-login        # Sign in as the GitHub owner for Terraform bootstrap"
	@echo "  make cloudsql-proxy  # Run the Cloud SQL Auth Proxy on localhost:5433"
	@echo "  make cloud-db-shell  # psql as forseti_owner through the running proxy"
	@echo "  make tf-bootstrap    # Owner-only identity/bootstrap apply and remote-state migration"
	@echo "  make tf-fmt          # Format Terraform configuration"
	@echo "  make tf-validate dir=bootstrap|main # Validate Terraform without cloud access"
	@echo "  make tf-init dir=bootstrap|main # Initialize Terraform"
	@echo "  make tf-plan dir=bootstrap|main # Plan Terraform; workloads apply only in CI"
	@echo "  make infra-check     # Offline Terraform, workflow, policy and IAM documentation checks"
	@echo "  make test-infra      # Offline owner-tooling and policy regression tests"
	@echo "  make iam-doc         # Regenerate access tables from identities.json"
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
	$(DOCKER_COMPOSE) run --rm --build --user $(HOST_USER) \
		-v "$$PWD/migrations:/app/migrations" \
		app python -m alembic revision --autogenerate -m "$(name)"
endif

db-shell: check-compose
	@$(DOCKER_COMPOSE) exec postgresql psql -U "$${POSTGRES_USER:-user}" -d "$${POSTGRES_DB:-forseti}"

test-runtime: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		-e APP_ENV=local -e DEBUG_LLM_IO=false \
		-e ALLOW_PIPELINE_OVERRIDE=false -e RAG_FAIL_LOUD=false \
		-e FUNDAMENTAL_AGENT_MODE=off -e TECHNICAL_AGENT_MODE=off \
		-e CRITIC_MODE=off -e PIPELINE_MODE=linear -e SYNTHESIZER_CONTRACT=legacy \
		-v "$$PWD/tests:/app/tests" \
		app python -m pytest tests/test_runtime.py tests/test_settings.py -q

test: check-compose
	@if [ -z "$(DOCKER_COMPOSE)" ]; then echo "Error: Neither 'docker compose' nor 'docker-compose' is available."; exit 1; fi
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-e APP_ENV=local \
		-e PIPELINE_MODE=linear \
		-e CRITIC_MODE=off\
		-e ALLOW_PIPELINE_OVERRIDE=false \
		-e FUNDAMENTAL_AGENT_MODE=off \
		-e TECHNICAL_AGENT_MODE=off \
		-e SYNTHESIZER_CONTRACT=legacy \
		-e DEBUG_LLM_IO=false \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/infra:/app/infra:ro" \
		-v "$$PWD/docs:/app/docs:ro" \
		-v /var/run/docker.sock:/var/run/docker.sock \
		app python -m pytest tests --cov=app --cov=agents --cov-report=term-missing --cov-fail-under=70 \
			-W "ignore:SelectableGroups dict interface is deprecated. Use select.:DeprecationWarning" \
			-W "ignore:BaseAgentConfig is deprecated and will be removed in future versions.:DeprecationWarning"

test-live-preflight: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		-e APP_ENV=local \
		-e PIPELINE_MODE=linear \
		-e CRITIC_MODE=off \
		-e ALLOW_PIPELINE_OVERRIDE=false \
		-e FUNDAMENTAL_AGENT_MODE=off \
		-e TECHNICAL_AGENT_MODE=off \
		-e SYNTHESIZER_CONTRACT=legacy \
		-e DEBUG_LLM_IO=false \
		-v "$$PWD/tests:/app/tests" \
		app python -m pytest tests/test_live_preflight.py -q

replay: check-compose
	@if [ -z "$(RUN_ID)" ]; then echo "Error: RUN_ID is required. Run 'make replay RUN_ID=<run id from the trace>'"; exit 1; fi
	$(DOCKER_COMPOSE) run --rm \
		-e DEBUG_LLM_IO_DIR=$${DEBUG_LLM_IO_DIR:-/tmp/forseti-llm-io} \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/tests:/app/tests" \
		-v "$${DEBUG_LLM_IO_DIR:-/tmp/forseti-llm-io}:$${DEBUG_LLM_IO_DIR:-/tmp/forseti-llm-io}" \
		app python -m scripts.replay_run --run-id "$(RUN_ID)" --json

ingest: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		app python -m app.ingestion.run --source all
	make ingest-rag

ingest-earnings: check-compose
	$(DOCKER_COMPOSE) run --rm --build app python -m app.ingestion.run --source earnings

ingest-rag: check-compose
	$(if $(ticker),	$(DOCKER_COMPOSE) run --rm app python -m app.rag.cli --ticker $(ticker),$(DOCKER_COMPOSE) run --rm app python -m app.rag.cli --all-active)

rag-coverage: check-compose
	@if [ -z "$(ticker)" ]; then echo "Error: ticker is required. Run 'make rag-coverage ticker=NVDA [live=1]'"; exit 1; fi
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		app python -m app.rag.cli --ticker "$(ticker)" --coverage --json $(if $(live),--live,)

analyze: check-compose
	@if [ -z "$(ticker)" ]; then \
		echo "Error: ticker is required. Run 'make analyze ticker=NVDA [mode=agentic|linear]'"; \
		exit 1; \
	fi
	@curl -s -X POST "http://127.0.0.1:8000/analyze?include_trace=true$(if $(mode),&pipeline=$(mode),)" \
		-H 'content-type: application/json' \
		-d '{"ticker":"$(ticker)"}' | python3 -m json.tool

fundamental-context: check-compose
	@if [ -z "$(ticker)" ]; then \
		echo "Error: ticker is required. Run 'make fundamental-context ticker=NVDA [as_of=2026-09-08]'"; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.build_fundamental_context --ticker "$(ticker)" $(if $(as_of),--as-of "$(as_of)",) --json

technical-context: check-compose
	@if [ -z "$(ticker)" ]; then \
		echo "Error: ticker is required. Run 'make technical-context ticker=NVDA [as_of=2026-09-08]'"; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.build_technical_context --ticker "$(ticker)" $(if $(as_of),--as-of "$(as_of)",) --json

critique-context: check-compose
	@if [ -z "$(ticker)" ]; then \
		echo "Error: ticker is required. Run 'make critique-context ticker=NVDA [as_of=2026-09-08]'"; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.build_critique_context --ticker "$(ticker)" $(if $(as_of),--as-of "$(as_of)",)

assess-fundamentals: check-compose
	@if [ -z "$(ticker)" ]; then \
		echo "Error: ticker is required. Run 'make assess-fundamentals ticker=NVDA [as_of=2026-09-08]'"; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.assess_fundamentals --ticker "$(ticker)" $(if $(as_of),--as-of "$(as_of)",) --shadow --json

assess-technicals: check-compose
	@if [ -z "$(ticker)" ]; then \
		echo "Error: ticker is required. Run 'make assess-technicals ticker=NVDA [as_of=2026-09-08]'"; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.assess_technicals --ticker "$(ticker)" $(if $(as_of),--as-of "$(as_of)",) --json

review-draft: check-compose
	@if [ -z "$(ticker)" ]; then \
		echo "Error: ticker is required. Run 'make review-draft ticker=NVDA [as_of=2026-09-08] [live=1]'"; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.review_draft --ticker "$(ticker)" $(if $(as_of),--as-of "$(as_of)",) $(if $(live),--live,)

synthesize-draft: check-compose
	@if [ -z "$(ticker)" ]; then \
		echo "Error: ticker is required. Run 'make synthesize-draft ticker=NVDA [as_of=2026-09-08]'"; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.synthesize_draft --ticker "$(ticker)" $(if $(as_of),--as-of "$(as_of)",)

eval-synthesizer: check-compose
	$(DOCKER_COMPOSE) run --no-deps --rm --build \
		--env-from-file .env \
		-e FUNDAMENTAL_AGENT_MODE=off \
		-e TECHNICAL_AGENT_MODE=off \
		-e CRITIC_MODE=off \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.eval_synthesizer --json

eval-synthesizer-live: check-compose
	@if [ "$(CONFIRM_COST)" != "1" ]; then \
		echo "Error: eval-synthesizer-live requires CONFIRM_COST=1 because it makes live model calls."; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --no-deps --rm --build \
		--env-from-file .env \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.eval_synthesizer --live --confirm-cost "$(CONFIRM_COST)" --json

eval-fundamental-agent: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-e TECHNICAL_AGENT_MODE=off \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.eval_fundamental_agent --json

eval-fundamental-agent-live: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-e TECHNICAL_AGENT_MODE=off \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.eval_fundamental_agent --live --confirm-cost "$(CONFIRM_COST)" --json

fundamental-shadow-report: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.eval_fundamental_agent --shadow-report --json

eval-critic: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-e FUNDAMENTAL_AGENT_MODE=off \
		-e TECHNICAL_AGENT_MODE=off \
		-e SYNTHESIZER_CONTRACT=legacy \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.eval_critic --json

eval-critic-live: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-e FUNDAMENTAL_AGENT_MODE=off \
		-e TECHNICAL_AGENT_MODE=off \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.eval_critic --live --confirm-cost "$(CONFIRM_COST)" --json

reports-dir:
	mkdir -p reports/trust

preflight-dir:
	mkdir -p reports/preflight

live-preflight: check-compose preflight-dir
	@if [ "$(CONFIRM_COST)" != "yes" ]; then \
		echo "Error: live-preflight requires CONFIRM_COST=yes because it makes live model calls."; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build --user $(HOST_USER) \
		--env-from-file .env \
		-v "$$PWD/reports:/app/reports" \
		app python -m scripts.live_preflight --confirm-cost "$(CONFIRM_COST)" $(if $(TICKERS),--tickers "$(TICKERS)",)

gcloud-login:
	@adc_path="$$HOME/.config/gcloud/application_default_credentials.json"; \
	if [ -d "$$adc_path" ] && [ -z "$$(find "$$adc_path" -mindepth 1 -maxdepth 1 -print -quit)" ]; then \
		rmdir "$$adc_path"; \
	fi
	gcloud config set project forseti-505116
	gcloud auth login --update-adc --no-launch-browser

cloud-logout:
	@adc_path="$$HOME/.config/gcloud/application_default_credentials.json"; \
	if [ -f "$$adc_path" ]; then \
		gcloud auth application-default revoke --quiet; \
	else \
		echo "No application-default credentials to revoke"; \
	fi
	@active_accounts="$$(gcloud auth list --format='value(account)')"; \
	if [ -n "$$active_accounts" ]; then \
		gcloud auth revoke --all --quiet; \
	else \
		echo "No gcloud accounts to revoke"; \
	fi
	@if command -v gh >/dev/null 2>&1 && env -u GITHUB_TOKEN -u GH_TOKEN gh auth status --hostname github.com >/dev/null 2>&1; then \
		env -u GITHUB_TOKEN -u GH_TOKEN gh auth logout --hostname github.com; \
	else \
		echo "No stored GitHub login to revoke"; \
	fi

critic-shadow-report: check-compose reports-dir
	$(DOCKER_COMPOSE) run --rm --build --user $(HOST_USER) \
		--env-from-file .env \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/reports:/app/reports" \
		app python -m scripts.eval_critic --shadow-report --json $(if $(SAMPLE),--sample "$(SAMPLE)",) $(if $(OUT),--out "$(OUT)",)

eval-technical-agent: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-e FUNDAMENTAL_AGENT_MODE=off \
		-e CRITIC_MODE=off \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.eval_technical_agent --json

eval-technical-agent-live: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		--env-from-file .env \
		-e TEST_DATABASE_URL=$(TEST_DATABASE_URL) \
		-e FUNDAMENTAL_AGENT_MODE=off \
		-e CRITIC_MODE=off \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		app python -m scripts.eval_technical_agent --live --confirm-cost "$(CONFIRM_COST)" --json

technical-shadow-report: check-compose reports-dir
	$(DOCKER_COMPOSE) run --rm --build --user $(HOST_USER) \
		--env-from-file .env \
		-e FUNDAMENTAL_AGENT_MODE=off \
		-e CRITIC_MODE=off \
		-v "$$PWD/tests:/app/tests" \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/reports:/app/reports" \
		app python -m scripts.eval_technical_agent --shadow-report --json $(if $(SAMPLE),--sample "$(SAMPLE)",) $(if $(OUT),--out "$(OUT)",)

technical-backtest: check-compose reports-dir
	$(DOCKER_COMPOSE) run --rm --build --user $(HOST_USER) \
		--env-from-file .env \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/reports:/app/reports" \
		app python -m scripts.technical_backtest --report-dir reports/trust $(if $(HORIZON_DAYS),--horizon-days "$(HORIZON_DAYS)",) --json

technical-shadow-batch: check-compose reports-dir
	@if [ "$(CONFIRM_COST)" != "yes" ]; then \
		echo "Error: technical-shadow-batch requires CONFIRM_COST=yes because it makes live network/model calls."; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build --user $(HOST_USER) \
		--env-from-file .env \
		-e TECHNICAL_AGENT_MODE=shadow \
		-e FUNDAMENTAL_AGENT_MODE=$(if $(FUNDAMENTAL_AGENT_MODE),$(FUNDAMENTAL_AGENT_MODE),off) \
		-e CRITIC_MODE=$(if $(CRITIC_MODE),$(CRITIC_MODE),off) \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/reports:/app/reports" \
		app python -m scripts.trust_e2e --matrix scripts/trust_tickers.json --report-dir reports/trust --repeat "$(if $(REPEAT),$(REPEAT),1)"

critic-shadow-batch: check-compose reports-dir
	@if [ "$(CONFIRM_COST)" != "yes" ]; then \
		echo "Error: critic-shadow-batch requires CONFIRM_COST=yes because it makes live network/model calls."; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build --user $(HOST_USER) \
		--env-from-file .env \
		-e CRITIC_MODE=shadow \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/reports:/app/reports" \
		app python -m scripts.trust_e2e --matrix scripts/trust_tickers.json --report-dir reports/trust --repeat "$(if $(REPEAT),$(REPEAT),1)"

trust-e2e: check-compose reports-dir
	@if [ "$(CONFIRM_COST)" != "yes" ]; then \
		echo "Error: trust-e2e requires CONFIRM_COST=yes because it makes live network/model calls."; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build --user $(HOST_USER) \
		--env-from-file .env \
		-e CRITIC_MODE=off \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/reports:/app/reports" \
		app python -m scripts.trust_e2e --matrix scripts/trust_tickers.json --report-dir reports/trust

trust-consistency: check-compose reports-dir
	@if [ "$(CONFIRM_COST)" != "yes" ]; then \
		echo "Error: trust-consistency requires CONFIRM_COST=yes because it makes live network/model calls."; \
		exit 1; \
	fi
	$(DOCKER_COMPOSE) run --rm --build --user $(HOST_USER) \
		--env-from-file .env \
		-e CRITIC_MODE=off \
		-e FUNDAMENTAL_AGENT_MODE=enforced \
		-v "$$PWD/scripts:/app/scripts" \
		-v "$$PWD/reports:/app/reports" \
		app python -m scripts.trust_e2e --matrix scripts/trust_tickers.json --report-dir reports/trust --repeat "$(if $(REPEAT),$(REPEAT),5)"

up: check-compose
	$(DOCKER_COMPOSE) up --force-recreate app

adk-web: check-compose
	$(DOCKER_COMPOSE) run --rm --build -p 8010:8010 app python -m google.adk.cli web agents --host 0.0.0.0 --port 8010

down: check-compose
	$(DOCKER_COMPOSE) down

lint: check-compose
	$(DOCKER_COMPOSE) run --rm --build -v "$$PWD/tests:/app/tests" -v "$$PWD/scripts:/app/scripts" app python -m flake8 app agents tests scripts/eval_synthesizer.py scripts/live_preflight.py scripts/synthesize_draft.py --count --select=E9,F63,F7,F82 --show-source --statistics
	$(DOCKER_COMPOSE) run --rm -v "$$PWD/tests:/app/tests" -v "$$PWD/scripts:/app/scripts" app python -m flake8 app agents tests scripts/eval_synthesizer.py scripts/live_preflight.py scripts/synthesize_draft.py --count --max-complexity=10 --max-line-length=127 --statistics
	$(DOCKER_COMPOSE) run --rm -v "$$PWD/scripts:/app/scripts" app python -m flake8 scripts/terraform_cli.py scripts/check_terraform_policy.py scripts/iam_doc.py --max-complexity=10 --max-line-length=127

typecheck: check-compose
	$(DOCKER_COMPOSE) run --rm --build \
		-v "$$PWD/pyproject.toml:/app/pyproject.toml:ro" \
		app python -m mypy --explicit-package-bases --follow-imports=skip app agents
	$(DOCKER_COMPOSE) run --rm --no-deps infra-tools python -m mypy \
		--explicit-package-bases --follow-imports=skip \
		scripts/terraform_cli.py scripts/check_terraform_policy.py scripts/iam_doc.py

check: lint typecheck

lock:
	docker run --rm -v "$$PWD:/work" -w /work python:3.12-slim sh -c '\
		pip install --quiet pip-tools && \
		pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --output-file=requirements.txt pyproject.toml && \
		pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --extra dev --output-file=requirements-dev.txt pyproject.toml'

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

CLOUD_SQL_INSTANCE ?= forseti-505116:europe-west4:forseti-pg

cloudsql-proxy: check-compose
	$(DOCKER_COMPOSE) run --rm --no-deps cloudsql-proxy --address 127.0.0.1 --port 5433 $(CLOUD_SQL_INSTANCE)

cloud-db-shell: check-compose
	@PGPASSWORD="$$(gcloud secrets versions access latest --secret=forseti-owner-database-url --project=forseti-505116 | sed -E 's|^[^:]+://[^:]+:([^@]+)@.*|\1|')" \
		$(DOCKER_COMPOSE) run --rm --no-deps -e PGPASSWORD cloud-psql psql -h 127.0.0.1 -p 5433 -U forseti_owner -d forseti

.PHONY: gh-login tf-fmt tf-validate tf-init tf-plan tf-bootstrap iam-doc infra-check test-infra

gh-login:
	env -u GITHUB_TOKEN -u GH_TOKEN GH_PROMPT_DISABLED=1 gh auth login --hostname github.com --web --scopes repo

tf-fmt tf-validate tf-init tf-plan:
	@python3 scripts/terraform_cli.py $(patsubst tf-%,%,$@) --dir "$(if $(dir),$(dir),main)"

tf-bootstrap:
	@python3 scripts/terraform_cli.py bootstrap

iam-doc: check-compose
	$(DOCKER_COMPOSE) run --rm --no-deps --user $(HOST_USER) infra-tools python -m scripts.iam_doc

infra-check: check-compose
	$(DOCKER_COMPOSE) run --rm --no-deps actionlint .github/workflows/infra.yml
	$(DOCKER_COMPOSE) run --rm --no-deps --user $(HOST_USER) terraform fmt -check -recursive
	@for layer in bootstrap main; do \
		$(DOCKER_COMPOSE) run --rm --no-deps --user $(HOST_USER) terraform -chdir=$$layer init -backend=false -lockfile=readonly && \
		$(DOCKER_COMPOSE) run --rm --no-deps --user $(HOST_USER) terraform -chdir=$$layer validate && \
		$(DOCKER_COMPOSE) run --rm --no-deps --user $(HOST_USER) terraform -chdir=$$layer test && \
		$(DOCKER_COMPOSE) run --rm --no-deps tflint --chdir=$$layer --config=/workspace/infra/terraform/.tflint.hcl || exit $$?; \
	done
	$(DOCKER_COMPOSE) run --rm --no-deps infra-tools sh -c \
		'python -m scripts.check_terraform_policy && python -m scripts.iam_doc --check'

test-infra: check-compose
	$(DOCKER_COMPOSE) run --rm --no-deps infra-tools python -m pytest tests/test_terraform.py -q