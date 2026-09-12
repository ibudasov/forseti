# Fundamental Analysis Context

Forseti builds one immutable `FundamentalAnalysisRequest` per ticker/as-of snapshot before any Fundamental Analyst model call. The request is deterministic, serializable, and bounded so replay/debug tooling can inspect the exact evidence pack that the analyst consumed.

## Schema version

- `schema_version`: `1.0`
- `run_id`: workflow run identifier
- `context_hash`: SHA-256 hash over the canonical JSON payload excluding `run_id` and `context_hash`
- `ticker`, `company_name`, `sector`, `currency`
- `snapshot_at`: deterministic cutoff timestamp for the request
- `as_of_date`: date used to filter fundamentals and evidence
- `deterministic_result`: the step-01 deterministic fundamental baseline
- `metric_series`: annual and quarterly observation history with stable metric IDs and provenance
- `evidence_chunks`: curated text evidence with stable chunk IDs and source metadata
- `coverage`: explicit presence, missingness, truncation, and recency metadata
- `allowed_adjustment_min`, `allowed_adjustment_max`: initial bounded analyst adjustment range `[-2, 2]`

## Stable IDs

### Metric series IDs

Observation-backed metric IDs use:

`<metric_name>:<fiscal_period>:<period_end>:<unit-or-none>:<accession-or-na>`

This keeps annual and quarterly observations distinct while preserving filing provenance.

### Evidence chunk IDs

Evidence chunk IDs come from the persisted `document_chunk.id`. The serialized request never includes embeddings or raw provider payloads.

## Deterministic budgets

- Annual history per metric: last `5` authoritative periods
- Quarterly history per metric: last `8` authoritative periods
- Evidence chunks per retrieval question: up to `3`
- Total serialized evidence text budget: `12,000` characters
- Evidence stale threshold: `180` days before `snapshot_at`

When the builder trims history or evidence, it sets `coverage.metric_series_truncated`, `coverage.evidence_truncated`, and the relevant `coverage.question_coverage[*].truncated` flags.

## Retrieval intents

The evidence pack is assembled with these explicit intents:

1. revenue growth sustainability;
2. cash-flow and margin drivers;
3. concentration and regulatory risks;
4. guidance versus historical trend;
5. one-off or accounting distortions;
6. liquidity, debt-maturity, dilution, and capex risk.

The builder prefers primary filing evidence, then earnings-call material, then news. It deduplicates chunk IDs across all questions, filters out evidence published after `snapshot_at`, and promotes source diversity before taking lower-priority duplicates.

## Missing-data semantics

- Missing latest fundamentals produce a valid request with a deterministic baseline whose rules become `unknown`.
- Missing historical observations appear in `coverage.required_metrics_missing`.
- Empty evidence still produces a valid request with `coverage.selected_chunk_count = 0`.
- Missing or stale evidence is explicit in coverage and does not itself invent a negative signal.

## Canonical hashing

`context_hash` is computed from sorted, whitespace-stable JSON and excludes:

- `run_id`
- `context_hash`

That makes equivalent payloads hash-identical across replay runs even when the workflow run ID changes.

## Developer command

Use the Docker-wrapped helper:

```bash
make fundamental-context ticker=NVDA as_of=2026-09-08
```

It prints the full JSON request so the same snapshot can be diffed or stored for offline replay.
