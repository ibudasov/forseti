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

This runs the housekeeping command (`python -m app.jobs.housekeeping`) inside the `app`
container against the PostgreSQL service. The compose superuser `user` acts as owner and
`.env` stays unchanged. Run a single step with `make housekeeping step=extensions|migrate|roles|collation`.
Locally the `roles` step is skipped unless `APP_DB_PASSWORD` is set.

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
| Housekeeping job (`NullPool`, at most 2 at a time) | 2 |
| Cloud SQL Auth Proxy sessions (`make cloud-db-shell`) | 1 |
| Total | 18 (≤ 20 of about 25, leaving headroom) |

Additional worker processes, simultaneous jobs, database URLs, or rolling-revision
overlap add pools. Account for these before raising instance limits or pool sizes.

Unix-socket URLs are supported without a TCP host or port:

```text
postgresql://USER:PASSWORD@/forseti?host=/cloudsql/PROJECT:REGION:INSTANCE
```

Percent-encode credentials and inject the URL from Secret Manager. Attach the
Cloud SQL instance to the Cloud Run service/job so the socket exists.

## Roles and privileges

| Role | Used by | Rights | Why |
| --- | --- | --- | --- |
| `forseti_app` | API, daily refresh job, on-demand ops jobs (`forseti-database-url`) | `CONNECT`, `USAGE` on schema `public`, `SELECT/INSERT/UPDATE/DELETE` on all tables, `USAGE/SELECT` on all sequences. No DDL, no `TRUNCATE`. | A bug or misuse of the API must not be able to drop tables or alter the database. |
| `forseti_owner` | Only the private `forseti-housekeeping` Cloud Run Job (`forseti-owner-database-url`) and the owner's `make cloud-db-shell` | Owner of the schema and database (`cloudsqlsuperuser`). | `CREATE EXTENSION vector`, `ALTER TABLE`, `ALTER DATABASE ... REFRESH COLLATION VERSION` need owner rights. |

The application never runs `ALTER DATABASE`; the per-connection collation listener is gone.

### How the roles are created

`forseti_owner` is the only user created through the Cloud SQL API (Terraform), because
that is what makes it a `cloudsqlsuperuser` member. `forseti_app` is created **only** by the
housekeeping `roles` step, never by hand or through the Cloud SQL API (API-created users
get `cloudsqlsuperuser`). The step runs
`CREATE ROLE ... LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE` (or `ALTER ROLE` to rotate the
password) with `APP_DB_PASSWORD`, quoted by the driver and never logged, then grants
the privileges above and sets `ALTER DEFAULT PRIVILEGES FOR ROLE <owner>` so tables created
by later migrations are covered automatically. Add `TRUNCATE` only if ingestion needs it.

### Housekeeping job

`python -m app.jobs.housekeeping [--step all|extensions|migrate|roles|collation] [--dry-run] [--reindex]`
runs idempotent steps and logs one `housekeeping_completed` event:

1. `extensions`: `CREATE EXTENSION IF NOT EXISTS vector`.
2. `migrate`: Alembic `upgrade head`, connecting with `HOUSEKEEPING_DATABASE_URL` (falls back to `DATABASE_URL` locally).
3. `roles`: the role and grants described above.
4. `collation`: on a `datcollversion` mismatch, refreshes the version and warns; `--reindex` also runs `REINDEX DATABASE`.

In the cloud it is the Cloud Run Job `forseti-housekeeping`: no URL, no ingress, no scheduler.
Only `forseti-deployer` (deploy pipeline) and the owner can run it:

```bash
gcloud run jobs execute forseti-housekeeping --region europe-west4 --wait
```

### Rotating the `forseti_app` password

1. Bump `credentials_version` in Terraform so new secret versions are written (`*_wo_version`).
2. Run the housekeeping job (the `roles` step applies the new password).
3. Redeploy the API and jobs so they read the new `forseti-database-url` version.

Use `/livez` for startup/liveness: it performs no database I/O. `/healthz` checks
the shared pool with `SELECT 1`; in cloud mode its only fields are `app: ok` and
`db: ok|unreachable`. Failures are logged server-side; local mode retains connection
diagnostics. The response remains HTTP 200 for compatibility, so readiness monitors
must inspect `db`. Liveness must not depend on database availability.

## Cloud SQL

Terraform (`infra/terraform/main/sql.tf`, `secrets.tf`) manages the `forseti-pg`
instance: PostgreSQL 15 (same major version as the local `pgvector/pgvector:pg15`
image), `ENTERPRISE` edition, `db-f1-micro`, zonal, 10 GB `PD_SSD` with
autoresize up to 50 GB, daily backups (7 retained), point-in-time recovery off,
maintenance on Sundays at 03:00 UTC, and deletion protection on.

### Connection model

The instance has a public IP with **no authorized networks** and
`ssl_mode = ENCRYPTED_ONLY`. It is reachable only through Cloud SQL connectors
and the Auth Proxy, which authorize through IAM (`roles/cloudsql.client`). No VPC
or Private Service Access is needed. Cloud Run mounts the unix socket at
`/cloudsql/<connection_name>`.

Owner access from a workstation (after `make gcloud-login`):

```bash
make cloudsql-proxy   # terminal 1: proxy on localhost:5433
make cloud-db-shell   # terminal 2: psql as forseti_owner, password read from Secret Manager
```

### Roles and secrets

| Role | Created by | Purpose |
| --- | --- | --- |
| `forseti_owner` | Terraform (Cloud SQL API, so it joins `cloudsqlsuperuser`) | Owns the schema; used only by housekeeping (GCP-3b) and owner sessions |
| `forseti_app` | Housekeeping job with SQL (GCP-3b); Terraform does **not** create it | Runtime role with data read/write privileges only |

| Secret | Content | Accessors |
| --- | --- | --- |
| `forseti-database-url` | `forseti_app` URL over the unix socket | `forseti-api`, `forseti-jobs` |
| `forseti-owner-database-url` | `forseti_owner` URL | None until GCP-3b grants `forseti-housekeeping` |
| `forseti-app-db-password` | `forseti_app` password | None until GCP-3b grants `forseti-housekeeping` |
| `forseti-alpha-vantage-api-key` | Added by the owner | `forseti-api`, `forseti-jobs` |

Add the Alpha Vantage key once:

```bash
printf '%s' "$KEY" | gcloud secrets versions add forseti-alpha-vantage-api-key --data-file=-
```

Passwords are generated with `ephemeral "random_password"` and sent through
write-only arguments (`password_wo`, `secret_data_wo`), so neither state nor
plans contain them. Bump `credentials_version` to rotate all of them together
(housekeeping then re-syncs `forseti_app`). Passwords have no special characters
and are also `urlencode()`d in the URLs.

### Sizing

`db-f1-micro` allows about 25 connections; the budget above (18, with pool 3 +
overflow 2 per API instance) keeps usage at or below 20 and leaves headroom for
Cloud SQL's own connections.

### Backup and restore

```bash
gcloud sql backups create --instance=forseti-pg
gcloud sql backups list --instance=forseti-pg
gcloud sql backups restore BACKUP_ID --restore-instance=forseti-pg
```

### Cost

About $9–12/month all-in. Shared-core tiers have no SLA; that is acceptable for
a private dev environment.
