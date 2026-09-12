# Fundamental observations

Issue #63 adds a normalized `fundamental_observation` store alongside the
existing latest-snapshot `fundamental` projection.

## Winner selection

When the same canonical metric and reporting period appear multiple times, the
authoritative observation is selected deterministically by:

1. latest `filed_at`
2. then latest `accession_number`
3. then lexicographically greatest `source_url`
4. then highest database `id` for already-persisted ties

This lets amended filings such as `10-Q/A` supersede older values without
depending on SEC payload order.

## Stored provenance

Each observation records:

- canonical `metric_name`
- decimal `value`
- `unit`
- `period_start` and `period_end`
- `fiscal_year` and `fiscal_period`
- `form_type`
- `filed_at`
- `accession_number`
- exact `source_concept`
- `source_url`
- `is_derived`
- `derivation`

Reported SEC facts and derived metrics are stored separately. Derived metrics
carry `is_derived=true` and a human-readable derivation string.

## Backfill

Replay stored SEC payloads without refetching:

```bash
docker compose run --rm app python -m app.ingestion.run --source fundamentals-backfill
```

Replay stored payloads for one ticker:

```bash
docker compose run --rm app python -m app.ingestion.run --source fundamentals-backfill --ticker NVDA
```

Replay stored payloads and then refetch fresh SEC data:

```bash
docker compose run --rm app python -m app.ingestion.run --source fundamentals-backfill --ticker NVDA --refetch-fundamentals
```

The backfill path is idempotent because inserts use the observation uniqueness
constraint and update the same logical row when the exact observation is seen
again.

## Rollback

Rollback the schema change with Alembic:

```bash
make migrate
```

Then downgrade manually to the previous revision if needed:

```bash
docker compose run --rm app alembic downgrade 0006_trace_observability
```
