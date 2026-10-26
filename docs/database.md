# Database Layer

## Schema Overview

This service persists raw market data, computed features, fundamentals, earnings, macro observations, and append-only trade recommendations.

Entities:

- `security`
  - Represents a tracked equity with normalised ticker, exchange, sector tag, and activation state.
- `price_bar`
  - Stores daily OHLCV price bars with a composite natural primary key `(security_id, bar_date)`.
- `fundamental`
  - Snapshots provider data with raw JSON payloads kept for auditability.
- `earnings_event`
  - Earnings report schedule and confirmation state, keyed by `(security_id, report_date)`.
- `macro_daily`
  - Daily macro observations such as VIX.
- `technical_feature`
  - Computed indicators derived from raw price bars.
- `recommendation`
  - Append-only trading recommendations, including full API response payload and engine version.
- `agent_runs`
  - One row per agentic workflow run, including persisted `fundamental_agent_effect`,
    `technical_agent_effect`, and `critic_effect` guardrail outcomes as nullable `JSONB` columns. `synthesizer_effect` (contract,
    prompt version, fallback used, proposed vs final decision, validation failures, memo hash) is
    written only when `SYNTHESIZER_CONTRACT=typed` and is `NULL` for legacy runs.
    `as_of_date` is the analysis date supplied by the request; its `as_of_date_source` is
    `request` or `created_at_backfill` for migrated legacy rows. Both columns are nullable
    when no analysis date was supplied. The `(ticker, as_of_date)` index supports date-scoped
    run lookups. See [`docs/agent-trace.md`](agent-trace.md) for the trace fields and SQL examples.

## Key design constraints

- Raw and computed data are stored separately.
- Recommendations are append-only; historic rows are never updated.
- Ingestion is idempotent for natural keys using PostgreSQL `ON CONFLICT` semantics.
- External payloads are persisted in `JSONB`.
- All timestamps are UTC-aware.

## Local development

### Environment

Copy the example environment file:

```bash
cp .env.example .env
```

Update values if needed.

### Start Docker Compose

```bash
docker-compose up -d postgresql
```

The `app` service can also be started with:

```bash
docker-compose up -d app
```

### Run migrations

From the repository root:

```bash
make migrate
```

This runs Alembic inside the `app` container and applies migrations to the PostgreSQL service.

### Create a new migration

```bash
make migration name="your_migration_name"
```

That command will generate a new Alembic revision stub in `migrations/versions`.

### Database shell

Open a PostgreSQL shell attached to the running service:

```bash
make db-shell
```

## Project files

- `app/db/models.py` — SQLModel table definitions
- `app/db/repository.py` — typed repository helpers and idempotent upsert logic
- `app/settings.py` — application settings reading `DATABASE_URL`
- `alembic.ini` / `migrations/env.py` — Alembic configuration
- `migrations/versions/0001_initial_schema.py` — initial schema migration
- `migrations/versions/0002_pgvector_extension.py` — pgvector extension stub

## Connection reuse and Cloud SQL

`get_engine()` caches one SQLAlchemy engine per database URL per process, including
when called through the API dependency or from different modules. Each engine uses
a small connection pool rather than a single connection shared by concurrent
requests. PostgreSQL defaults are:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `DB_POOL_SIZE` | 3 | Persistent connections per process |
| `DB_MAX_OVERFLOW` | 2 | Temporary extra connections under load |
| `DB_POOL_RECYCLE_SECONDS` | 1800 | Recycle aged connections on checkout |

Pre-ping is enabled. API lifespan shutdown disposes all pools after SIGTERM;
job entrypoints dispose pools in a `finally` block, including after failures.
`dispose_engines()` also clears the cache for test isolation.

For Cloud SQL `db-f1-micro` (approximately 25 connections), cap the API at two
instances with one worker process each and concurrency 4:

| Consumer | Maximum connections |
| --- | ---: |
| API: 2 instances × (3 + 2) | 10 |
| Refresh job: one process | 5 |
| Housekeeping (configure smaller pools explicitly) | 2 |
| Owner `psql` session | 1 |
| Total | 18 (≤ 20, leaving headroom) |

Additional worker processes, simultaneous jobs, database URLs, or rolling-revision
overlap add pools. Account for these before raising instance limits or pool sizes.

Unix-socket URLs are supported without a TCP host or port:

```text
postgresql://USER:PASSWORD@/forseti?host=/cloudsql/PROJECT:REGION:INSTANCE
```

Percent-encode credentials and inject the URL from Secret Manager. Attach the
Cloud SQL instance to the Cloud Run service/job so the socket exists. No database
role, migration, or collation-refresh behavior changes are included here.

Use `/livez` for startup/liveness: it performs no database I/O. `/healthz` checks
the shared pool with `SELECT 1`; in cloud mode its only fields are `app: ok` and
`db: ok|unreachable`. Failures are logged server-side; local mode retains connection
diagnostics. The response remains HTTP 200 for compatibility, so readiness monitors
must inspect `db`. Liveness must not depend on database availability.
